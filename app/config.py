"""Конфигурация приложения из переменных окружения."""

from functools import lru_cache

from pydantic import Field, field_validator
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

    # ЮKassa / подписка
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_test_mode: bool = Field(default=True, alias="YOOKASSA_TEST_MODE")
    subscription_price_rub: int = Field(default=299, alias="SUBSCRIPTION_PRICE_RUB")
    subscription_period_days: int = Field(default=365, alias="SUBSCRIPTION_PERIOD_DAYS")
    free_trial_uses: int = Field(default=3, alias="FREE_TRIAL_USES")

    # Google Sheets (опционально)
    google_service_account_json: str = Field(
        default="",
        alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )
    google_sheets_spreadsheet_id: str = Field(
        default="",
        alias="GOOGLE_SHEETS_SPREADSHEET_ID",
    )

    # Поиск AO3/Ficbook после extract
    platform_search_budget: int = Field(default=15, alias="PLATFORM_SEARCH_BUDGET")

    @field_validator("yookassa_test_mode", mode="before")
    @classmethod
    def _parse_bool(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return True
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

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
