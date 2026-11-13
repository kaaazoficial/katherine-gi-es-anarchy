"""Tests for environment-backed Katherine settings."""
from pathlib import Path

import pytest

from katherine_config import KatherineSettings


def test_settings_use_safe_defaults(monkeypatch):
    # CI/test runners may inject DATA_DIR or other process-level settings.
    # Clear every supported setting to make this a genuine defaults test.
    for name in ("PORT", "COMMAND_PREFIX", "ADMIN_UIDS", "ADMINUIDS", "DATA_DIR", "TTS_VOICE"):
        monkeypatch.delenv(name, raising=False)
    settings = KatherineSettings(_env_file=None)
    assert settings.port == 3000
    assert settings.command_prefix == "!"
    assert settings.data_dir == Path("./data")
    assert settings.tts_voice == "es-CL-CatalinaNeural"


def test_settings_validate_and_normalize_values():
    settings = KatherineSettings(
        _env_file=None,
        PORT="4567",
        COMMAND_PREFIX="??",
        DATA_DIR="/tmp/katherine-test",
        TTS_VOICE=" es-ES-ElviraNeural ",
        ADMIN_UIDS="uid-1,uid-2",
    )
    assert settings.port == 4567
    assert settings.command_prefix == "??"
    assert settings.data_dir == Path("/tmp/katherine-test")
    assert settings.tts_voice == "es-ES-ElviraNeural"
    assert settings.admin_uids == "uid-1,uid-2"


def test_settings_reject_invalid_port_and_prefix():
    with pytest.raises(ValueError):
        KatherineSettings(_env_file=None, PORT="70000")
    with pytest.raises(ValueError):
        KatherineSettings(_env_file=None, COMMAND_PREFIX="bad prefix")
