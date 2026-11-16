"""
Application configuration

This module exposes a singleton `settings` instance that can be imported
throughout the project (e.g., from config import settings).
"""
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthAPISettings(BaseSettings):
    # Load .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # FastAPI application
    APP_NAME: str = "AuthAPI"
    APP_DESCRIPTION: str = "Manage authentication and product licensing"
    APP_VERSION: str = "1.0"
    # API url infos: api.fcpxstudio.shop/<API_NAME>/<API_VERSION>/
    API_NAME: str = "auth"
    API_VERSION: str = "v1" 

    # Database: DB_* variables are read by the shared `db` package (db.DatabaseSettings)

    # Licensing
    LICENSE_PRIVATE_KEY: SecretStr
    LICENSE_TOKEN_FORMAT_VERSION: int = 1

    # Paddle
    PADDLE_API_KEY: SecretStr
    PADDLE_ENVIRONMENT: Literal["sandbox", "production"] = "sandbox"
    PADDLE_ORDER_WEBHOOK_SECRET: SecretStr



settings = AuthAPISettings() # type: ignore