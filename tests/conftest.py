"""Shared test configuration. Never use production data or credentials."""
import os
import tempfile

# main.py reads these values during import. Keep CI fully isolated and offline.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="katherine-tests-")
os.environ["BOT_TOKEN"] = ""
os.environ["WEBHOOK_SECRET"] = ""
os.environ["COMMUNITY_BOT_TOKEN"] = ""
os.environ["COMMUNITY_WEBHOOK_SECRET"] = ""
