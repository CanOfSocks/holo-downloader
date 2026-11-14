"""
Offline tests. A temporary config (no webhook, no schedules, files in a temp folder) is set up
before any project module is imported, because modules read the config when imported.
Run with:  python -m pytest
"""
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="holo-tests-")
_CONFIG_PATH = os.path.join(_TEST_DIR, "config.toml")

with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
    f.write(f"""
[channel_ids_to_match]
test = "UCxxxxxxxxxxxxxxxxxxxxxx"

[title_filter]
"UCtitle" = "(?i)karaoke"

[description_filter]
"UCtitle" = "(?i)singing"
"UCdesc" = "(?i)singing"

[download_options]
temp_dir = {repr(os.path.join(_TEST_DIR, "temp"))}
unarchived_tempdir = {repr(os.path.join(_TEST_DIR, "unarchived"))}
log_level = "WARNING"

[cron_schedule]
""")

os.environ["CONFIG_FILE"] = _CONFIG_PATH
os.environ["HISTORY_DB"] = os.path.join(_TEST_DIR, "stream_history.db")
