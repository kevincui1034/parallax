"""Kling image-to-video adapter via GMI Cloud.

Generates explode-view and turntable animation videos from a source image.
"""

import logging
from dataclasses import dataclass

from ..config import load_settings
from .gmi_ie import submit_request, poll_request, extract_media_urls

logger = logging.getLogger(__name__)


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
    duration: str = "5",
    negative_prompt: str = "blurry, low quality, distorted, text, labels, logos, watermark",
) -> VideoGenResult:
    """Generate a video via Kling image-to-video through GMI Cloud.

    Returns VideoGenResult with video_url on success.
    """
    settings = load_settings()

    if not settings.gmi_api_key:
        return VideoGenResult(success=False, status="blocked", error="GMI_API_KEY not configured")

    model = settings.video_model_id
    payload = {
        "image": image_url,
        "prompt": prompt,
        "duration": duration,
        "negative_prompt": negative_prompt,
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
