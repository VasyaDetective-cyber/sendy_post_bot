from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    super_admin_id: int
    database_url: str = "sqlite+aiosqlite:///data/sendy.db"
    timezone: str = "Europe/Kyiv"
    scheduler_interval_sec: int = 20
    log_level: str = "INFO"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
