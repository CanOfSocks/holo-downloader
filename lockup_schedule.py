"""
Scheduled start times for upcoming videos in flat playlist listings.

YouTube lists playlist videos (including the uploads/members playlists used by channel scans) as
`lockupViewModel` items. yt-dlp marks upcoming ones `is_upcoming` but doesn't give a
`release_timestamp`, because the item has no timestamp field, only display text such as
"Scheduled for 11/14/26, 11:00 AM". yt-dlp requests pages with hl=en and timeZone=UTC, so that
text is in UTC and in US date order. install() wraps yt-dlp's parser to fill in
`release_timestamp` from it, which lets channel scans apply the look-ahead window and tell
downloads about schedule changes without a full extraction of each upcoming video.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# "11/14/26, 11:00 AM" (the space before AM/PM may be a narrow no-break space)
_SCHEDULED_PATTERN = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{2}|\d{4}),?\s+(\d{1,2}):(\d{2})\s*([AP]M)', re.IGNORECASE)


def parse_scheduled_text(text: str) -> Optional[int]:
    """Epoch seconds of a "Scheduled for M/D/YY, h:mm AM" style string (UTC), or None if it doesn't match."""
    match = _SCHEDULED_PATTERN.search(text or "")
    if not match:
        return None
    month, day, year, hour, minute, meridiem = match.groups()
    year_value = int(year) + (2000 if len(year) == 2 else 0)
    hour_value = int(hour) % 12 + (12 if meridiem.upper() == "PM" else 0)
    try:
        moment = datetime(year_value, int(month), int(day), hour_value, int(minute), tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(moment.timestamp())


def _metadata_texts(view_model: dict) -> Iterable[str]:
    rows = (((view_model.get('metadata') or {}).get('lockupMetadataViewModel') or {})
            .get('metadata') or {}).get('contentMetadataViewModel', {}).get('metadataRows') or []
    for row in rows:
        for part in row.get('metadataParts') or []:
            content = ((part or {}).get('text') or {}).get('content')
            if isinstance(content, str):
                yield content


def scheduled_start_from_lockup(view_model: dict) -> Optional[int]:
    for text in _metadata_texts(view_model):
        timestamp = parse_scheduled_text(text)
        if timestamp is not None:
            return timestamp
    return None


_installed = False


def install() -> None:
    """Wrap yt-dlp's lockupViewModel parser (once) so upcoming entries get a release_timestamp."""
    global _installed
    if _installed:
        return
    try:
        from yt_dlp.extractor.youtube._tab import YoutubeTabBaseInfoExtractor
        original = YoutubeTabBaseInfoExtractor._extract_lockup_view_model
    except (ImportError, AttributeError):
        logger.warning("yt-dlp's playlist parser has changed, scheduled start times won't be read from channel scans")
        return

    def extract_lockup_view_model(self: Any, view_model: dict, *args: Any, **kwargs: Any):
        entry = original(self, view_model, *args, **kwargs)
        if isinstance(entry, dict) and entry.get('live_status') == 'is_upcoming' and not entry.get('release_timestamp'):
            try:
                entry['release_timestamp'] = scheduled_start_from_lockup(view_model)
            except Exception:
                # Never let this break the listing itself
                logger.debug("Unable to read the scheduled start of %s", entry.get('id'), exc_info=True)
        return entry

    YoutubeTabBaseInfoExtractor._extract_lockup_view_model = extract_lockup_view_model
    _installed = True
