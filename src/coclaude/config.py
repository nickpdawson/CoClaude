"""Runtime configuration, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    public_url: str = "http://localhost:8788"
    host: str = "0.0.0.0"
    port: int = 8788
    db_path: str = "data/coclaude.db"

    google_client_id: str = ""
    google_client_secret: str = ""

    smtp_host: str = "mail.dzsec.net"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    mail_from: str = "coclaude@dzsec.net"

    # Guards the one-time Google bootstrap route.
    admin_setup_key: str = ""

    owner_email: str = ""
    owner_name: str = "Owner"
    owner_initials: str = "ND"


@lru_cache
def settings() -> Settings:
    return Settings()
