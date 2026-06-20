"""Загрузка и валидация конфигурации из .env."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")

    db_driver: str = Field(default="sqlite", alias="DB_DRIVER")
    sqlite_path: str = Field(default="bot.db", alias="SQLITE_PATH")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="botuser", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="botdb", alias="POSTGRES_DB")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: str = Field(default="logs/bot.log", alias="LOG_FILE")

    throttle_rate: float = Field(default=0.7, alias="THROTTLE_RATE")
    auto_init_db: bool = Field(default=True, alias="AUTO_INIT_DB")

    bot_admins: str = Field(default="", alias="BOT_ADMINS")

    google_creds_path: str = Field(default="", alias="GOOGLE_CREDS_PATH")
    google_sheet_id: str = Field(default="", alias="GOOGLE_SHEET_ID")

    @field_validator("bot_token")
    @classmethod
    def _check_token(cls, v: str) -> str:
        if ":" not in v or not v.split(":", 1)[0].isdigit():
            raise ValueError(
                "BOT_TOKEN имеет неверный формат (ожидается вид '123456789:ABC-DEF...')."
            )
        return v

    @property
    def admin_ids(self) -> set[int]:
        """Множество id владельцев бота (для команд в личке)."""
        return {
            int(x)
            for x in self.bot_admins.replace(" ", "").split(",")
            if x.strip().lstrip("-").isdigit()
        }

    @property
    def database_url(self) -> str:
        """Возвращает async-DSN в зависимости от выбранного драйвера."""
        if self.db_driver == "postgres":
            return (
                f"postgresql+asyncpg://{self.postgres_user}:"
                f"{self.postgres_password}@{self.postgres_host}:"
                f"{self.postgres_port}/{self.postgres_db}"
            )
        # SQLite по умолчанию
        path = Path(self.sqlite_path).resolve()
        return f"sqlite+aiosqlite:///{path}"


settings = Settings()
