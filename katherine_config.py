"""Validated, environment-backed configuration for Katherine."""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KatherineSettings(BaseSettings):
    """Validate only stable process settings; secrets remain outside the model."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    port: int = Field(default=3000, ge=1, le=65535, validation_alias="PORT")
    command_prefix: str = Field(
        default="!", min_length=1, max_length=3, validation_alias="COMMAND_PREFIX"
    )
    admin_uids: str = Field(
        default="",
        validation_alias=AliasChoices("ADMIN_UIDS", "ADMINUIDS"),
    )
    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")
    tts_voice: str = Field(
        default="es-CL-CatalinaNeural", validation_alias="TTS_VOICE"
    )

    @field_validator("command_prefix", mode="before")
    @classmethod
    def normalize_command_prefix(cls, value: object) -> str:
        prefix = str(value or "").strip() or "!"
        if len(prefix) > 3 or any(character.isspace() for character in prefix):
            raise ValueError("COMMAND_PREFIX debe tener 1-3 caracteres sin espacios.")
        return prefix

    @field_validator("tts_voice", mode="before")
    @classmethod
    def normalize_tts_voice(cls, value: object) -> str:
        return str(value or "").strip() or "es-CL-CatalinaNeural"


SETTINGS = KatherineSettings()
