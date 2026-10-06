import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from s3_upload import (
    S3Settings,
    build_s3_key,
    collect_files,
    collect_output_files,
    is_safe_child,
    kind_prefix,
    resolve_settings,
    upload_files,
)


class DummyConfig:
    def __init__(self, options=None):
        self._options = options or {}

    def get_s3_options(self):
        return self._options


class S3UploadHelpersTest(unittest.TestCase):
    def test_build_s3_key_strips_slashes(self):
        self.assertEqual(build_s3_key("hololive/", "/members/", "Channel/file.mp4"), "hololive/members/Channel/file.mp4")
        self.assertEqual(build_s3_key("", None, "file.mp4"), "file.mp4")
        self.assertEqual(build_s3_key("a\\b", "c"), "a/b/c")

    def test_kind_prefix_defaults(self):
        settings = S3Settings(enabled=True, members_prefix="members", unarchived_prefix="unarchived", community_prefix="community")
        self.assertEqual(kind_prefix(settings, "streams"), "")
        self.assertEqual(kind_prefix(settings, "members"), "members")
        self.assertEqual(kind_prefix(settings, "unarchived"), "unarchived")
        self.assertEqual(kind_prefix(settings, "community"), "community")

    def test_collect_output_files_uses_video_folder_not_entire_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "OtherChannel" / "other-video"
            other.mkdir(parents=True)
            (other / "other.mp4").write_bytes(b"nope")

            video_dir = root / "Channel" / "[20260101] Title (abc123)"
            video_dir.mkdir(parents=True)
            video = video_dir / "[20260101] Title [Channel] (abc123).mp4"
            sidecar = video_dir / "[20260101] Title [Channel] (abc123).info.json"
            video.write_bytes(b"video")
            sidecar.write_text("{}")

            stem = video_dir / "[20260101] Title [Channel] (abc123)"
            files = collect_output_files(stem, root)
            names = sorted(path.name for path in files)
            self.assertEqual(names, [
                "[20260101] Title [Channel] (abc123).info.json",
                "[20260101] Title [Channel] (abc123).mp4",
            ])

    def test_shallow_template_does_not_upload_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "Title (abc123).mp4"
            sibling = root / "Other (xyz).mp4"
            keep.write_bytes(b"keep")
            sibling.write_bytes(b"skip")
            files = collect_output_files(root / "Title (abc123)", root)
            self.assertEqual([path.name for path in files], ["Title (abc123).mp4"])

    def test_is_safe_child_rejects_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "Channel"
            child.mkdir()
            self.assertTrue(is_safe_child(child, root))
            self.assertFalse(is_safe_child(root, root))

    def test_collect_files_skips_dotfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "clip.mp4").write_bytes(b"ok")
            (folder / ".hidden").write_bytes(b"no")
            names = [path.name for path in collect_files(folder)]
            self.assertEqual(names, ["clip.mp4"])

    def test_resolve_settings_env_overrides(self):
        config = DummyConfig({"enabled": False, "bucket": "from-toml", "prefix": "toml"})
        with patch.dict(os.environ, {"S3_ENABLED": "true", "S3_BUCKET": "from-env", "S3_PREFIX": "env"}, clear=False):
            settings = resolve_settings(config)
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.bucket, "from-env")
            self.assertEqual(settings.prefix, "env")

    def test_empty_members_prefix_is_preserved(self):
        config = DummyConfig({"enabled": True, "bucket": "b", "members_prefix": ""})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("S3_ENABLED", None)
            settings = resolve_settings(config)
            self.assertEqual(settings.members_prefix, "")

    def test_dry_run_upload_builds_prefixed_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video_dir = root / "Channel" / "Title (abc123)"
            video_dir.mkdir(parents=True)
            video = video_dir / "Title (abc123).mp4"
            video.write_bytes(b"data")
            config = DummyConfig({
                "enabled": True,
                "bucket": "archive",
                "prefix": "hololive",
                "members_prefix": "members",
            })
            with patch.dict(os.environ, {"S3_ENABLED": "true", "S3_BUCKET": "", "S3_PREFIX": ""}, clear=False):
                os.environ.pop("S3_BUCKET", None)
                os.environ.pop("S3_PREFIX", None)
                result = upload_files(
                    [video],
                    root=root,
                    kind="members",
                    config=config,
                    dry_run=True,
                )
            self.assertTrue(result.ok)
            self.assertEqual(result.keys, ["hololive/members/Channel/Title (abc123)/Title (abc123).mp4"])


if __name__ == "__main__":
    unittest.main()
