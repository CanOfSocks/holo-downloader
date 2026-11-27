"""
Discord webhook notifications.

Messages are queued and sent by a single background thread, so a slow or rate-limited webhook
never blocks a download, and messages for a video arrive in the order they were queued.
"""
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from discord_webhook import DiscordEmbed, DiscordWebhook

import common
from getConfig import ConfigHandler

# If Discord is unreachable for a long time, drop new messages instead of using more and more memory
MAX_QUEUED_MESSAGES = 200
WEBHOOK_TIMEOUT = 30
# On shutdown, queued messages are still sent for this long, so "done"/"error" messages of stopping
# jobs aren't lost. Under gunicorn the exiting worker waits for them with flush() (see gunicorn.conf.py).
SHUTDOWN_SEND_SECONDS = 8

_ANSI_ESCAPE = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')

# status: (title, colour)
_VIDEO_STATUSES: dict[str, tuple[str, str]] = {
    "recording": ("Recording", "0011ff"),
    "waiting": ("Waiting", "ffff00"),
    "error": ("Error", "ff0000"),
    "done": ("Done", "00ff00"),
    "stalled": ("Stalled", "fc8803"),
}


@dataclass
class _Notification:
    url: str
    id: str
    status: str
    message: Optional[str]
    proxy: Any
    embed_info: Optional[Dict[str, Any]]


_queue: "queue.Queue[_Notification]" = queue.Queue(maxsize=MAX_QUEUED_MESSAGES)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_logger = common.initialize_logging(logger_name="discord")


def remove_ansi_escape_sequences(text: str) -> str:
    return _ANSI_ESCAPE.sub('', text)


def notify(id: str, status: str, message: Optional[str] = None, config: Optional[ConfigHandler] = None,
           embed_info: Optional[Dict[str, Any]] = None) -> None:
    """
    Queue a notification. Does nothing if no webhook is configured.
    `embed_info` is oEmbed info the caller already has, to avoid fetching it again.
    """
    config = config or ConfigHandler()
    url = config.get_discord_webhook()
    if not url:
        return
    if message is not None:
        message = remove_ansi_escape_sequences(message)
    try:
        _queue.put_nowait(_Notification(url, id, status, message, config.get_proxy(), embed_info))
    except queue.Full:
        _logger.warning("Discord notification queue is full, dropping '{0}' message for {1}".format(status, id))
        return
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="discord-notifier", daemon=True)
            _worker.start()


def _worker_loop() -> None:
    """
    Send queued notifications. On shutdown (kill_all), keep sending what is queued (including
    messages from jobs that are just stopping) for up to SHUTDOWN_SEND_SECONDS, then exit.
    """
    shutdown_deadline: Optional[float] = None
    while True:
        if common.kill_all.is_set() and shutdown_deadline is None:
            shutdown_deadline = time.monotonic() + SHUTDOWN_SEND_SECONDS
        if shutdown_deadline is not None and time.monotonic() >= shutdown_deadline:
            return
        try:
            notification = _queue.get(timeout=1 if shutdown_deadline is not None else 5)
        except queue.Empty:
            continue
        try:
            send_webhook(notification)
        except Exception:
            _logger.exception("Unable to send Discord notification")
        finally:
            _queue.task_done()


def flush(timeout: float) -> bool:
    """
    Wait up to `timeout` seconds for every queued notification to be sent (used on shutdown,
    as the sender is a daemon thread that ends with the process). Returns True if none are left.
    """
    deadline = time.monotonic() + timeout
    if _queue.unfinished_tasks:
        # The sender may have exited already (see _worker_loop), so make sure one is running
        _ensure_worker()
    with _queue.all_tasks_done:
        while _queue.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _queue.all_tasks_done.wait(remaining)
    return True


def _channel_embed(title: str, description: str, notification: _Notification) -> DiscordEmbed:
    embed = DiscordEmbed(title, description=description, color="ff0000")
    if notification.message:
        embed.add_embed_field(name="Error Message: {0}".format(notification.id), value=notification.message)
        embed.set_footer(text='Error Logger')
    return embed


def send_webhook(notification: _Notification) -> None:
    """Build and send one notification (runs on the worker thread)."""
    video_id = notification.id
    status = notification.status

    if status == "starting":
        embed = DiscordEmbed("Starting", description="Starting holo-downloader", color="fc8803")
    elif status == "membership-error":
        embed = _channel_embed("Membership error", "Error checking membership streams for [{0}](https://www.youtube.com/channel/{0}). \nCheck cookies!".format(video_id), notification)
    elif status == "playlist-error":
        embed = _channel_embed("Playlist error", "Error checking streams playlist for [{0}](https://www.youtube.com/channel/{0}). \nCheck cookies!".format(video_id), notification)
    else:
        title, color = _VIDEO_STATUSES.get(status, ("Notification", "03b2f8"))
        embed = _video_embed(title, color, notification)

    embed.set_timestamp()
    webhook = DiscordWebhook(notification.url, rate_limit_retry=True, timeout=WEBHOOK_TIMEOUT)
    webhook.add_embed(embed)
    webhook.execute()


def _video_embed(title: str, color: str, notification: _Notification) -> DiscordEmbed:
    video_id = notification.id
    info = notification.embed_info
    error: Optional[str] = None
    if not info:
        result = common.fetch_oembed(video_id, proxy=notification.proxy)
        info = result.data
        # 401/403/404 mean private, removed or restricted: shown as "not accessible" below
        if result.status_code not in (200, 401, 403, 404):
            error = result.error

    if info:
        embed = DiscordEmbed(title, description="[{0}](https://youtu.be/{1})".format(info.get('title', video_id), video_id), color=color)
        embed.set_author(name=info.get('author_name'), url=info.get('author_url'))
        embed.set_thumbnail(url=info.get('thumbnail_url') or "https://img.youtube.com/vi/{0}/maxresdefault.jpg".format(video_id))
    elif error:
        embed = DiscordEmbed(title, description="Video https://youtu.be/{0} is not accessible due to error: {1}".format(video_id, error[:250]), color=color)
    else:
        embed = DiscordEmbed(title, description="Video https://youtu.be/{0} is not accessible, perhaps it has been privated".format(video_id), color=color)

    if notification.message:
        embed.add_embed_field(name="Message: {0}".format(video_id), value=notification.message)
    return embed
