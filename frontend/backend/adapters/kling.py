"""Kling image-to-video adapter via GMI Cloud.

Generates explode-view and turntable animation videos from a source image.
"""

import base64
import logging
import mimetypes
import os
from dataclasses import dataclass

from ..config import load_settings
from .gmi_ie import submit_request, poll_request, extract_media_urls

logger = logging.getLogger(__name__)


def _image_to_base64(image_url: str) -> str:
    """Convert a local file path or file:// URL to a base64-encoded data string.

    GMI IE API expects the raw base64 string (no data: prefix) for the `image` field.
    """
    path = image_url.replace("file://", "")
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        mime_type = "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return b64


@dataclass
class VideoGenResult:
    success: bool
    video_url: str = ""
    status: str = "blocked"
    error: str = ""


async def generate_video(
    image_url: str,
    prompt: str,
    mode: str = "turntable",  # "explode" or "turntable"
    duration: str = "10",
    negative_prompt: str = "blurry, low quality, distorted, text, labels, logos, watermark",
) -> VideoGenResult:
    """Generate a video via Kling V3 Omni through GMI Cloud.

    Uses kling-v3-omni with image_list (first_frame) format, pro mode for 1080p.
    Returns VideoGenResult with video_url on success.
    """
    settings = load_settings()

    if not settings.gmi_api_key:
        return VideoGenResult(success=False, status="blocked", error="GMI_API_KEY not configured")

    model = settings.video_model_id

    # Convert local file paths to base64 data URIs for the Omni API
    image_data = image_url
    if image_url.startswith("file://") or (
        not image_url.startswith("http") and not image_url.startswith("data:")
        and os.path.exists(image_url.replace("file://", ""))
    ):
        try:
            b64 = _image_to_base64(image_url)
            mime_type, _ = mimetypes.guess_type(image_url.replace("file://", ""))
            if not mime_type:
                mime_type = "image/jpeg"
            image_data = f"data:{mime_type};base64,{b64}"
            logger.info("Kling: converted image to base64 data URI (%d chars)", len(image_data))
        except Exception as e:
            logger.error("Kling: failed to convert image to base64 — %s", e)
            return VideoGenResult(success=False, status="blocked", error=f"Image conversion failed: {e}")

    # Kling V3 Omni uses image_list with type field, pro mode for 1080p
    payload = {
        "prompt": prompt,
        "mode": "pro",
        "duration": duration,
        "sound": "off",
        "image_list": [
            {
                "image_url": image_data,
                "type": "first_frame",
            }
        ],
    }

    logger.info("Kling: submitting %s video request (model=%s)", mode, model)
    resp = await submit_request(model, payload)

    if "error" in resp:
        return VideoGenResult(success=False, status="blocked", error=f"Kling: {resp['error']}")

    request_id = resp.get("request_id") or resp.get("id")
    if not request_id:
        return VideoGenResult(success=False, status="blocked", error="Kling: no request_id returned")

    logger.info("Kling: %s request submitted, id=%s", mode, request_id)

    # Poll for completion
    result = await poll_request(request_id, max_wait_sec=settings.max_video_wait_sec)

    status = result.get("status", "").lower()
    if status in ("success", "completed", "succeeded"):
        urls = extract_media_urls(result)
        if urls:
            video_url = urls[0]
            logger.info("Kling: %s video completed — %s", mode, video_url)
            return VideoGenResult(success=True, video_url=video_url, status="completed")
        return VideoGenResult(success=False, status="blocked", error="Kling: no media URLs in response")
    else:
        error = result.get("error", "Unknown error")
        return VideoGenResult(success=False, status="blocked", error=f"Kling: {error}")
