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
    risc_allowed_audiences: str = ""
    risc_endpoint_path: str = "/api/security/risc/events"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    xai_api_key: str = ""
    openai_api_key: str = ""
    openai_analysis_models: str = "gpt-5,gpt-4.1,gpt-4o"
    persona_image_openai_model: str = "gpt-image-1"
    persona_image_google_model: str = "gemini-2.5-flash-image"
    persona_image_prefer_google_for_drawing: bool = False
    suno_api_key: str = ""
    fish_audio_api_key: str = ""
    elevenlabs_api_key: str = ""
    replicate_api_token: str = ""
    minimax_api_key: str = ""
    runpod_api_key: str = ""
    atlascloud_api_key: str = ""
    atlascloud_api_base_url: str = "https://api.atlascloud.ai/api/v1"
    atlascloud_seedance_t2v_model: str = "bytedance/seedance-2.0/text-to-video"
    atlascloud_seedance_i2v_model: str = "bytedance/seedance-2.0/image-to-video"
    atlascloud_wan_t2v_model: str = "alibaba/wan-2.7/text-to-video"
    atlascloud_wan_i2v_model: str = "alibaba/wan-2.6/image-to-video"
    mp_access_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    site_url: str = "https://criavideo.pro"
    levita_url: str = "https://levita.pro"
    levita_remove_vocals_url: str = ""
    levita_api_token: str = ""
    levita_stems_dir: str = "/opt/olevita/backend/stems"
    levita_uploads_dir: str = "/opt/olevita/backend/uploads"
    media_dir: str = "/opt/levita-video/media"
    tevoxi_api_url: str = "https://levita.pro"
    tevoxi_signup_url: str = "https://tevoxi.com"
    tevoxi_api_token: str = ""
    tevoxi_jwt_secret: str = ""
    tevoxi_jwt_user_id: int = 5
    tevoxi_jwt_email: str = "tgsantos66@hotmail.com"
    baixatudo_api_url: str = "https://baixatudo.pro"
    baixatudo_api_key: str = ""
    baixatudo_timeout_seconds: int = 120
    baixatudo_poll_interval_seconds: float = 2.5
    baixatudo_max_wait_seconds: int = 900
    similar_analysis_model: str = "gpt-4o"
    similar_scene_default_seconds: int = 5
    similar_scene_min_seconds: int = 5
    similar_scene_max_seconds: int = 15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
