"""Environment variable loading and credential safety.

Never logs key values. Only logs present=true/false.
"""

import os
from dataclasses import dataclass
from typing import Optional

_SECRET_NAMES = {
    "GMI_MAAS_API_KEY",
    "GMI_API_KEY",
    "GMI_IE_MODEL_API_KEY",
    "TAVILY_API_KEY",
    "LANGSMITH_API_KEY",
}


@dataclass
class Settings:
    # GMI MaaS (OpenAI-compatible) — for Gemini chat/vision
    gmi_maas_base_url: str
    gmi_maas_api_key: str
    gmi_models: str  # comma-separated, first is primary

    # GMI Inference Engine (image/video generation)
    gmi_api_key: str
    gmi_ie_model_api_key: str
    gmi_ie_base_url: str
    image_generate_model_id: str
    image_edit_model_id: str
    video_model_id: str

    # Web search (optional)
    tavily_api_key: str

    # Observability
    langsmith_tracing: bool
    langsmith_api_key: str
    langsmith_project: str

    # Storage
    artifact_public_base_url: str
    upload_dir: str

    # Runtime
    max_video_wait_sec: int
    frame_count: int


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("true", "1", "yes")


def load_settings() -> Settings:
    return Settings(
        gmi_maas_base_url=_get("GMI_MAAS_BASE_URL", "https://api.gmi-serving.com/v1"),
        gmi_maas_api_key=_get("GMI_MAAS_API_KEY"),
        gmi_models=_get("GMI_MODELS", "google/gemini-3.5-flash"),
        gmi_api_key=_get("GMI_API_KEY"),
        gmi_ie_model_api_key=_get("GMI_IE_MODEL_API_KEY"),
        gmi_ie_base_url=_get("GMI_IE_BASE_URL", "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey"),
        image_generate_model_id=_get("IMAGE_GENERATE_MODEL_ID", "gpt-image-2-generate"),
        image_edit_model_id=_get("IMAGE_EDIT_MODEL_ID", "gpt-image-2-edit"),
        video_model_id=_get("VIDEO_MODEL_ID", "kling-v3-image-to-video"),
        tavily_api_key=_get("TAVILY_API_KEY"),
        langsmith_tracing=_get_bool("LANGSMITH_TRACING", False),
        langsmith_api_key=_get("LANGSMITH_API_KEY"),
        langsmith_project=_get("LANGSMITH_PROJECT", "agent-visual-manual"),
        artifact_public_base_url=_get("ARTIFACT_PUBLIC_BASE_URL", ""),
        upload_dir=_get("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")),
        max_video_wait_sec=_get_int("MAX_VIDEO_WAIT_SEC", 300),
        frame_count=_get_int("FRAME_COUNT", 24),
    )


def check_credential(name: str) -> dict:
    val = os.environ.get(name, "")
    return {"name": name, "present": bool(val)}


def credential_report() -> list[dict]:
    all_creds = sorted(_SECRET_NAMES | {
        "GMI_MAAS_BASE_URL",
        "GMI_MODELS",
        "GMI_IE_BASE_URL",
        "IMAGE_GENERATE_MODEL_ID",
        "IMAGE_EDIT_MODEL_ID",
        "VIDEO_MODEL_ID",
        "ARTIFACT_PUBLIC_BASE_URL",
        "UPLOAD_DIR",
    })
    return [check_credential(n) for n in all_creds]


def get_secret(name: str) -> Optional[str]:
    if name not in _SECRET_NAMES:
        raise ValueError(f"{name} is not a known secret")
    val = os.environ.get(name, "")
    return val if val else None
