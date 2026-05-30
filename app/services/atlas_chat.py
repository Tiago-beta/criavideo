"""Atlas Cloud OpenAI-compatible chat helpers."""

import os

import openai

from app.config import get_settings

settings = get_settings()

_DEFAULT_ATLAS_CHAT_BASE_URL = "https://api.atlascloud.ai/v1"
_DEFAULT_ATLAS_PROMPT_MODEL = "deepseek-ai/deepseek-v4-flash"


def get_atlas_chat_api_key() -> str:
    key = (settings.atlascloud_api_key or "").strip()
    if key:
        return key
    return (os.getenv("ATLASCLOUD_API_KEY") or "").strip()


def get_atlas_chat_base_url() -> str:
    configured = (settings.atlascloud_openai_api_base_url or "").strip()
    return (configured or _DEFAULT_ATLAS_CHAT_BASE_URL).rstrip("/")


def get_atlas_prompt_model() -> str:
    configured = (settings.atlascloud_prompt_model or "").strip()
    return configured or _DEFAULT_ATLAS_PROMPT_MODEL


def create_atlas_chat_async_client() -> openai.AsyncOpenAI:
    api_key = get_atlas_chat_api_key()
    if not api_key:
        raise RuntimeError("ATLASCLOUD_API_KEY not configured")
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=get_atlas_chat_base_url(),
    )