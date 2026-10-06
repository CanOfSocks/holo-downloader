#!/usr/local/bin/python
"""Upload finished archive files to Amazon S3 or an S3-compatible store."""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


@dataclass
class S3Settings:
    enabled: bool
    bucket: str = ""
    prefix: str = ""
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    storage_class: Optional[str] = None
    acl: Optional[str] = None
    extra_args: Dict[str, Any] = field(default_factory=dict)
    delete_local_after_upload: bool = False
    skip_existing: bool = True
    upload_community_posts: bool = True
    streams_prefix: str = ""
    members_prefix: str = "members"
    unarchived_prefix: str = "unarchived"
    community_prefix: str = "community"
    addressing_style: str = "auto"
    verify_ssl: bool = True
    multipart_threshold_mb: int = 64
    multipart_chunksize_mb: int = 16
    max_concurrency: int = 4
    dry_run: bool = False


@dataclass
class UploadResult:
    enabled: bool
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    deleted_local: bool = False
    error: Optional[str] = None
    keys: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (not self.enabled) or (self.error is None and self.failed == 0)


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def _first_str(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _prefix_option(options: Dict[str, Any], key: str, default: str) -> str:
    if key in options and options[key] is not None:
        return str(options[key]).replace("\\", "/").strip("/")
    return default


def build_s3_key(*parts: Optional[str]) -> str:
    """Join S3 key segments without a leading slash."""
    pieces: List[str] = []
    for part in parts:
        if part is None:
            continue
        cleaned = str(part).replace("\\", "/").strip("/")
        if cleaned:
            pieces.append(cleaned)
    return "/".join(pieces)


def kind_prefix(settings: S3Settings, kind: str) -> str:
    mapping = {
        "streams": settings.streams_prefix,
        "members": settings.members_prefix,
        "unarchived": settings.unarchived_prefix,
        "community": settings.community_prefix,
    }
    return mapping.get(kind, "") or ""


def is_safe_child(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return path.resolve() != root.resolve()


def collect_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))


def collect_output_files(output_stem: Path, root: Path) -> List[Path]:
    """
    Collect files that belong to a finished download.

    livestream_dl writes into the parent of the output stem when the template
    is nested (the recommended layout). For a shallow template the parent is
    the output root, so only files matching the stem name are uploaded.
    """
    parent = output_stem.parent
    try:
        parent_is_root = parent.resolve() == root.resolve()
    except OSError:
        parent_is_root = True

    if parent.is_dir() and not parent_is_root:
        nested = [p for p in parent.rglob("*") if p.is_file() and not p.name.startswith(".")]
        if nested:
            return sorted(nested)
    if parent.exists():
        return sorted(p for p in parent.glob(output_stem.name + "*") if p.is_file())
    return []


def find_files_by_video_id(root: Path, video_id: str) -> List[Path]:
    if not video_id or not root.exists():
        return []
    for dirpath, dirnames, filenames in os.walk(root):
        folder_name = os.path.basename(dirpath)
        if video_id in folder_name:
            return collect_files(Path(dirpath))
        matches = [Path(dirpath) / name for name in filenames if video_id in name]
        if matches:
            return sorted(matches)
        # Avoid walking huge unrelated trees named without the id.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    return []


def _config(config: Optional[Any] = None):
    if config is not None:
        return config
    from getConfig import ConfigHandler
    return ConfigHandler()


def resolve_settings(config: Optional[Any] = None, dry_run: bool = False) -> S3Settings:
    config = _config(config)
    options = dict(config.get_s3_options() or {})

    enabled_env = _env_flag("S3_ENABLED")
    enabled = enabled_env if enabled_env is not None else _as_bool(options.get("enabled", False))

    return S3Settings(
        enabled=enabled,
        bucket=_first_str(os.getenv("S3_BUCKET"), options.get("bucket")) or "",
        prefix=_first_str(os.getenv("S3_PREFIX"), options.get("prefix")) or "",
        region=_first_str(os.getenv("AWS_REGION"), os.getenv("AWS_DEFAULT_REGION"), options.get("region")),
        endpoint_url=_first_str(os.getenv("AWS_ENDPOINT_URL"), os.getenv("S3_ENDPOINT_URL"), options.get("endpoint_url")),
        access_key_id=_first_str(os.getenv("AWS_ACCESS_KEY_ID"), options.get("access_key_id"), options.get("aws_access_key_id")),
        secret_access_key=_first_str(os.getenv("AWS_SECRET_ACCESS_KEY"), options.get("secret_access_key"), options.get("aws_secret_access_key")),
        session_token=_first_str(os.getenv("AWS_SESSION_TOKEN"), options.get("session_token")),
        storage_class=_first_str(options.get("storage_class")),
        acl=_first_str(options.get("acl")),
        extra_args=dict(options.get("extra_args") or {}),
        delete_local_after_upload=_as_bool(options.get("delete_local_after_upload"), False),
        skip_existing=_as_bool(options.get("skip_existing"), True),
        upload_community_posts=_as_bool(options.get("upload_community_posts"), True),
        streams_prefix=_prefix_option(options, "streams_prefix", ""),
        members_prefix=_prefix_option(options, "members_prefix", "members"),
        unarchived_prefix=_prefix_option(options, "unarchived_prefix", "unarchived"),
        community_prefix=_prefix_option(options, "community_prefix", "community"),
        addressing_style=(_first_str(options.get("addressing_style"), "auto") or "auto").lower(),
        verify_ssl=_as_bool(options.get("verify_ssl"), True),
        multipart_threshold_mb=int(options.get("multipart_threshold_mb") or 64),
        multipart_chunksize_mb=int(options.get("multipart_chunksize_mb") or 16),
        max_concurrency=int(options.get("max_concurrency") or 4),
        dry_run=dry_run,
    )


def relative_output_template(config: Any, info_dict: Dict[str, Any], channel_id: Optional[str] = None) -> str:
    from yt_dlp import YoutubeDL

    options = {
        "outtmpl": config.get_ytdlp(channel_id or info_dict.get("channel_id")),
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(options) as ydl:
        return str(ydl.prepare_filename(info_dict)).replace("%", "％")


class _ProgressLogger:
    def __init__(self, name: str, size: int, logger: logging.Logger):
        self.name = name
        self.size = max(size, 0)
        self.logger = logger
        self.seen = 0
        self.last_pct = -10

    def __call__(self, bytes_amount: int) -> None:
        self.seen += bytes_amount
        if self.size <= 0:
            return
        pct = int(self.seen * 100 / self.size)
        if pct >= self.last_pct + 10 or self.seen >= self.size:
            self.last_pct = pct
            self.logger.info("S3 upload %s: %s%% (%s / %s bytes)", self.name, min(pct, 100), self.seen, self.size)


def _import_boto():
    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import ClientError
    except ImportError as exc:
        raise RuntimeError("S3 upload requires boto3. Install it with: pip install boto3") from exc
    return boto3, TransferConfig, BotoConfig, ClientError


def create_client(settings: S3Settings):
    boto3, _, BotoConfig, _ = _import_boto()

    session_kwargs: Dict[str, Any] = {}
    if settings.access_key_id:
        session_kwargs["aws_access_key_id"] = settings.access_key_id
        session_kwargs["aws_secret_access_key"] = settings.secret_access_key or ""
        if settings.session_token:
            session_kwargs["aws_session_token"] = settings.session_token
    if settings.region:
        session_kwargs["region_name"] = settings.region

    session = boto3.session.Session(**session_kwargs)

    boto_kwargs: Dict[str, Any] = {
        "retries": {"max_attempts": 10, "mode": "standard"},
        "connect_timeout": 10,
        "read_timeout": 300,
        "signature_version": "s3v4",
    }
    if settings.addressing_style in ("path", "virtual"):
        boto_kwargs["s3"] = {"addressing_style": settings.addressing_style}

    # Newer boto3 default checksums break some S3-compatible endpoints (R2, MinIO).
    if settings.endpoint_url:
        boto_kwargs["request_checksum_calculation"] = "when_required"
        boto_kwargs["response_checksum_validation"] = "when_required"

    try:
        boto_config = BotoConfig(**boto_kwargs)
    except TypeError:
        boto_kwargs.pop("request_checksum_calculation", None)
        boto_kwargs.pop("response_checksum_validation", None)
        boto_config = BotoConfig(**boto_kwargs)
        if settings.endpoint_url:
            os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
            os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

    client_kwargs: Dict[str, Any] = {"config": boto_config}
    if settings.endpoint_url:
        client_kwargs["endpoint_url"] = settings.endpoint_url
    if settings.verify_ssl is False:
        client_kwargs["verify"] = False
    if settings.region:
        client_kwargs["region_name"] = settings.region

    return session.client("s3", **client_kwargs)


def _guess_content_type(path: Path) -> Optional[str]:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type


def _object_same_size(client, ClientError, bucket: str, key: str, size: int) -> bool:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return int(head.get("ContentLength") or -1) == size


def _build_extra_args(settings: S3Settings, path: Path, video_id: Optional[str] = None) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    content_type = _guess_content_type(path)
    if content_type:
        extra["ContentType"] = content_type
    if settings.storage_class:
        extra["StorageClass"] = settings.storage_class
    if settings.acl:
        extra["ACL"] = settings.acl
    metadata = {}
    if video_id:
        metadata["video-id"] = video_id
    metadata["source"] = "holo-downloader"
    extra["Metadata"] = metadata
    extra.update(settings.extra_args or {})
    return extra


def upload_files(
    files: Iterable[Path],
    *,
    root: Path,
    kind: str,
    config: Optional[Any] = None,
    logger: Optional[logging.Logger] = None,
    video_id: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> UploadResult:
    logger = logger or logging.getLogger("s3_upload")
    settings = resolve_settings(config, dry_run=dry_run)
    result = UploadResult(enabled=settings.enabled)

    if not settings.enabled:
        logger.debug("S3 upload disabled; skipping")
        return result
    if not settings.bucket:
        result.error = "S3 is enabled but no bucket is configured (s3.bucket or S3_BUCKET)"
        logger.error(result.error)
        return result

    file_list = [Path(f) for f in files if Path(f).is_file()]
    if not file_list:
        logger.warning("S3 upload: no files found to upload%s", f" for {video_id}" if video_id else "")
        return result

    extra_prefix = kind_prefix(settings, kind)
    client = None
    transfer_config = None
    ClientError = Exception
    if not settings.dry_run:
        _, TransferConfig, _, ClientError = _import_boto()
        client = create_client(settings)
        transfer_config = TransferConfig(
            multipart_threshold=max(settings.multipart_threshold_mb, 8) * 1024 * 1024,
            multipart_chunksize=max(settings.multipart_chunksize_mb, 8) * 1024 * 1024,
            max_concurrency=max(settings.max_concurrency, 1),
            use_threads=True,
        )

    if stats is not None:
        stats["status"] = "Uploading"

    root_resolved = root.resolve()
    for path in file_list:
        try:
            relative = path.resolve().relative_to(root_resolved)
        except ValueError:
            relative = Path(path.name)
        key = build_s3_key(settings.prefix, extra_prefix, relative.as_posix())
        size = path.stat().st_size
        uri = f"s3://{settings.bucket}/{key}"

        if settings.dry_run:
            logger.info("S3 dry-run: %s -> %s (%s bytes)", path, uri, size)
            result.uploaded += 1
            result.keys.append(key)
            continue

        try:
            if settings.skip_existing and _object_same_size(client, ClientError, settings.bucket, key, size):
                logger.info("S3 skip existing: %s", uri)
                result.skipped += 1
                result.keys.append(key)
                continue

            extra_args = _build_extra_args(settings, path, video_id=video_id)
            callback = _ProgressLogger(path.name, size, logger) if size >= 8 * 1024 * 1024 else None
            logger.info("S3 uploading %s -> %s (%s bytes)", path, uri, size)
            client.upload_file(
                Filename=str(path),
                Bucket=settings.bucket,
                Key=key,
                ExtraArgs=extra_args,
                Config=transfer_config,
                Callback=callback,
            )
            result.uploaded += 1
            result.keys.append(key)
        except Exception as exc:
            result.failed += 1
            logger.exception("S3 upload failed for %s: %s", path, exc)
            if result.error is None:
                result.error = f"{type(exc).__name__}: {exc}"

    if result.failed:
        if stats is not None:
            stats["status"] = "S3 Error"
        return result

    if settings.delete_local_after_upload and not settings.dry_run:
        result.deleted_local = _delete_uploaded_locals(file_list, root=root, logger=logger)

    if stats is not None:
        stats["status"] = "Finished"
    logger.info(
        "S3 upload complete%s: %s uploaded, %s skipped, %s failed",
        f" for {video_id}" if video_id else "",
        result.uploaded,
        result.skipped,
        result.failed,
    )
    return result


def _delete_uploaded_locals(files: List[Path], *, root: Path, logger: logging.Logger) -> bool:
    parents = {path.parent.resolve() for path in files}
    deleted = False
    # Prefer removing unique per-video folders rather than individual files at the root.
    for parent in parents:
        if is_safe_child(parent, root):
            try:
                import shutil

                shutil.rmtree(parent)
                logger.info("Removed local copy after S3 upload: %s", parent)
                deleted = True
            except OSError as exc:
                logger.warning("Could not remove %s after S3 upload: %s", parent, exc)
        else:
            for path in files:
                if path.parent.resolve() == parent:
                    try:
                        path.unlink()
                        logger.info("Removed local file after S3 upload: %s", path)
                        deleted = True
                    except OSError as exc:
                        logger.warning("Could not remove %s after S3 upload: %s", path, exc)
    return deleted


def output_root_and_stem(
    config: Any,
    kind: str,
    output_template: str,
) -> Tuple[Path, Path]:
    if kind == "members":
        return Path(config.get_membership_directory()), Path(config.get_membership_output_path(output_template))
    if kind == "unarchived":
        return Path(config.get_unarchived_folder()), Path(config.get_unarchived_output_path(output_template))
    if kind == "community":
        root = Path(config.get_community_tab_directory() or "")
        return root, root / output_template
    return Path(config.get_done_folder()), Path(config.get_done_output_path(output_template))


def upload_completed_download(
    *,
    config: Any,
    logger: logging.Logger,
    kind: str,
    output_template: Optional[str] = None,
    info_dict: Optional[Dict[str, Any]] = None,
    video_id: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> UploadResult:
    settings = resolve_settings(config)
    result = UploadResult(enabled=settings.enabled)
    if not settings.enabled:
        return result

    if not output_template and info_dict:
        output_template = relative_output_template(config, info_dict)
    if not output_template:
        result.error = "S3 upload skipped: no output path available"
        logger.error(result.error)
        return result

    root, stem = output_root_and_stem(config, kind, output_template)
    files = collect_output_files(stem, root)
    if not files and video_id:
        logger.info("S3 upload: output folder missing, searching %s for video id %s", root, video_id)
        files = find_files_by_video_id(root, video_id)

    return upload_files(
        files,
        root=root,
        kind=kind,
        config=config,
        logger=logger,
        video_id=video_id,
        stats=stats,
    )


def upload_directory(
    local_dir: str,
    *,
    config: Any,
    logger: logging.Logger,
    kind: str = "community",
    root: Optional[str] = None,
) -> UploadResult:
    settings = resolve_settings(config)
    result = UploadResult(enabled=settings.enabled)
    if not settings.enabled:
        return result
    if kind == "community" and not settings.upload_community_posts:
        logger.debug("S3 community-post upload disabled; skipping")
        result.enabled = False
        return result

    path = Path(local_dir)
    root_path = Path(root) if root else path.parent
    return upload_files(
        collect_files(path),
        root=root_path,
        kind=kind,
        config=config,
        logger=logger,
    )


def maybe_upload_download(downloader, info_dict: Optional[Dict[str, Any]] = None, kind: str = "streams") -> UploadResult:
    """Convenience wrapper used by the video downloaders."""
    config = getattr(downloader, "config", None) or _config()
    logger = getattr(downloader, "logger", None) or logging.getLogger("s3_upload")
    stats = getattr(getattr(downloader, "livestream_downloader", None), "stats", None)
    output_template = getattr(downloader, "outputFile", None)
    video_id = getattr(downloader, "id", None)
    if info_dict and info_dict.get("availability") == "subscriber_only":
        kind = "members"
    return upload_completed_download(
        config=config,
        logger=logger,
        kind=kind,
        output_template=output_template,
        info_dict=info_dict,
        video_id=video_id,
        stats=stats,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload archive files to S3")
    parser.add_argument("path", help="Local file or directory to upload")
    parser.add_argument("--kind", default="streams", choices=["streams", "members", "unarchived", "community"])
    parser.add_argument("--video-id", default=None)
    parser.add_argument("--root", default=None, help="Directory used as the S3 key root (defaults to parent)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from getConfig import ConfigHandler

    app_config = ConfigHandler()
    from common import initialize_logging

    cli_logger = initialize_logging(app_config, logger_name="s3_upload")
    target = Path(args.path)
    key_root = Path(args.root) if args.root else (target.parent if target.is_file() else target.parent)
    report = upload_files(
        collect_files(target) if target.is_dir() else [target],
        root=key_root,
        kind=args.kind,
        config=app_config,
        logger=cli_logger,
        video_id=args.video_id,
        dry_run=args.dry_run,
    )
    if not report.ok:
        raise SystemExit(1)
