"""GMI MaaS OpenAI-compatible client for Gemini 3.5 Flash.

Used for:
- Image understanding (vision)
- Part planning (structured JSON output)
- Explode/turntable prompt generation
"""

import base64
import json
import logging
import mimetypes
import os
from typing import Optional

import httpx

from .config import load_settings

logger = logging.getLogger(__name__)


def _file_to_data_uri(file_path: str) -> str:
    """Convert a local file to a base64 data URI."""
    file_path = file_path.replace("file://", "")
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "image/png"
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


async def gmi_vision(image_url: str, prompt: str) -> str:
    """Call GMI MaaS vision model with image + text prompt.

    Accepts either a URL or a local file path (converted to base64 data URI).
    Returns the text response, or empty string on failure.
    """
    settings = load_settings()
    if not settings.gmi_maas_api_key:
        logger.warning("gmi_vision: GMI_MAAS_API_KEY not configured")
        return ""

    models = [m.strip() for m in settings.gmi_models.split(",")]
    primary = models[0]

    # Convert local file paths to base64 data URIs
    img_src = image_url
    if image_url.startswith("file://") or (
        not image_url.startswith("http") and not image_url.startswith("data:")
    ):
        try:
            img_src = _file_to_data_uri(image_url)
        except Exception as e:
            logger.error("gmi_vision: could not convert file to base64 — %s", e)
            return ""

    payload = {
        "model": primary,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": img_src}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.4,
    }

    headers = {
        "Authorization": f"Bearer {settings.gmi_maas_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.gmi_maas_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.warning("gmi_vision: %s returned %d — %s", primary, resp.status_code, resp.text[:200])
                # Try fallback models
                for fallback in models[1:]:
                    payload["model"] = fallback
                    resp = await client.post(
                        f"{settings.gmi_maas_base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code < 400:
                        break
                else:
                    logger.error("gmi_vision: all models failed")
                    return ""

            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error("gmi_vision error: %s", e)
        return ""


async def gmi_chat(prompt: str, system: str = "") -> str:
    """Call GMI MaaS text-only model.

    Returns the text response, or empty string on failure.
    """
    settings = load_settings()
    if not settings.gmi_maas_api_key:
        logger.warning("gmi_chat: GMI_MAAS_API_KEY not configured")
        return ""

    models = [m.strip() for m in settings.gmi_models.split(",")]
    primary = models[0]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": primary,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.4,
    }

    headers = {
        "Authorization": f"Bearer {settings.gmi_maas_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.gmi_maas_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.warning("gmi_chat: %s returned %d — %s", primary, resp.status_code, resp.text[:200])
                for fallback in models[1:]:
                    payload["model"] = fallback
                    resp = await client.post(
                        f"{settings.gmi_maas_base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code < 400:
                        break
                else:
                    logger.error("gmi_chat: all models failed")
                    return ""

            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error("gmi_chat error: %s", e)
        return ""


def parse_json_response(text: str) -> Optional[dict]:
    """Try to extract JSON from a model response."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None
