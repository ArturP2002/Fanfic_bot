"""Конфигурация приложения из переменных окружения."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки FicBrain, читаемые из .env и окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://ficbrain:ficbrain_secret@localhost:5433/ficbrain",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_vision_model: str = Field(
        default="gpt-4o-mini",
        alias="OPENAI_VISION_MODEL",
    )

    apify_token: str = Field(default="", alias="APIFY_TOKEN")
    apify_instagram_actor: str = Field(
        default="apify/instagram-reel-scraper",
        alias="APIFY_INSTAGRAM_ACTOR",
    )
    apify_tiktok_actor: str = Field(
        default="clockworks/tiktok-scraper",
        alias="APIFY_TIKTOK_ACTOR",
    )

    video_max_frames: int = Field(default=12, alias="VIDEO_MAX_FRAMES")
    whisper_language: str = Field(default="ru", alias="WHISPER_LANGUAGE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_telegram_ids: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    tmp_dir: str = Field(default="data/tmp", alias="TMP_DIR")

    # Исходящий SOCKS5 (VPS в РФ): socks5h://user:pass@host:1080
    socks5_proxy: str = Field(default="", alias="SOCKS5_PROXY")

    @property
    def admin_ids(self) -> set[int]:
        """Список telegram id администраторов."""
        if not self.admin_telegram_ids.strip():
            return set()
        return {
            int(item.strip())
            for item in self.admin_telegram_ids.split(",")
            if item.strip().isdigit()
        }


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()
