import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://levita:password@localhost:5432/levita"
    jwt_secret: str = "change_me"
    auth_token_expiration_hours: int = 24 * 30
    google_ai_api_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    xai_api_key: str = ""
    openai_api_key: str = ""
    suno_api_key: str = ""
    fish_audio_api_key: str = ""
    mp_access_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    site_url: str = "https://criavideo.pro"
    levita_url: str = "https://levita.pro"
    levita_stems_dir: str = "/opt/olevita/backend/stems"
    levita_uploads_dir: str = "/opt/olevita/backend/uploads"
    media_dir: str = "/opt/levita-video/media"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
