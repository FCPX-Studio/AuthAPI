"""
Application configuration

This module exposes a singleton `settings` instance that can be imported
throughout the project (e.g., from config import settings).
"""
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthAPISettings(BaseSettings):
    # Load .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,  # never print secrets in validation errors
    )

    # FastAPI application
    APP_NAME: str = "AuthAPI"
    APP_DESCRIPTION: str = "Manage authentication and product licensing"
    APP_VERSION: str = "1.0"
    # API url infos: api.fcpxstudio.shop/<API_NAME>/<API_VERSION>/
    API_NAME: str = "auth"
    API_VERSION: str = "v1"

    # Database: DB_* variables are read by the shared `db` package (db.DatabaseSettings)
    # Redis: REDIS_* variables are read by the shared `events` package (events.RedisSettings)

    # Licensing
    LICENSE_PRIVATE_KEY: SecretStr
    LICENSE_TOKEN_FORMAT_VERSION: int = 1
    # Public label of the LICENSE_PRIVATE_KEY pair, written as `keyId` into signed tokens: the product
    # picks the matching public key from it. Change it together with the key pair (rotation)
    LICENSE_KEY_ID: str = "k1"


settings = AuthAPISettings() # type: ignore