"""Image generation/editing adapter via GMI Cloud.

Uses gpt-image-2-generate or gpt-image-2-edit for clean visual generation.
"""

import logging
from dataclasses import dataclass

from ..config import load_settings
from .gmi_ie import submit_request, poll_request, extract_media_urls

logger = logging.getLogger(__name__)


@dataclass
class ImageGenResult:
    success: bool
    image_url: str = ""
    status: str = "blocked"
    error: str = ""


async def generate_image(prompt: str, size: str = "1024x1024") -> ImageGenResult:
    """Generate an image from a text prompt via GMI Cloud."""
    settings = load_settings()
    if not settings.gmi_api_key:
        return ImageGenResult(success=False, status="blocked", error="GMI_API_KEY not configured")

    model = settings.image_generate_model_id
    payload = {
        "prompt": prompt,
        "size": size,
        "quality": "medium",
        "output_format": "png",
        "n": 1,
    }

    logger.info("ImageGen: submitting generate request (model=%s)", model)
    resp = await submit_request(model, payload)

    if "error" in resp:
        return ImageGenResult(success=False, status="blocked", error=f"ImageGen: {resp['error']}")

    request_id = resp.get("request_id") or resp.get("id")
    if not request_id:
        return ImageGenResult(success=False, status="blocked", error="ImageGen: no request_id returned")

    result = await poll_request(request_id, max_wait_sec=120)

    status = result.get("status", "").lower()
    if status in ("success", "completed", "succeeded"):
        urls = extract_media_urls(result)
        if urls:
            return ImageGenResult(success=True, image_url=urls[0], status="completed")
        return ImageGenResult(success=False, status="blocked", error="ImageGen: no media URLs")
    else:
        error = result.get("error", "Unknown error")
        return ImageGenResult(success=False, status="blocked", error=f"ImageGen: {error}")


async def edit_image(image_url: str, prompt: str, size: str = "1024x1024") -> ImageGenResult:
    """Edit an existing image via GMI Cloud."""
    settings = load_settings()
    if not settings.gmi_api_key:
        return ImageGenResult(success=False, status="blocked", error="GMI_API_KEY not configured")

    model = settings.image_edit_model_id
    payload = {
        "prompt": prompt,
        "image": image_url,
        "size": size,
        "quality": "medium",
        "n": 1,
    }

    logger.info("ImageGen: submitting edit request (model=%s)", model)
    resp = await submit_request(model, payload)

    if "error" in resp:
        return ImageGenResult(success=False, status="blocked", error=f"ImageEdit: {resp['error']}")

    request_id = resp.get("request_id") or resp.get("id")
    if not request_id:
        return ImageGenResult(success=False, status="blocked", error="ImageEdit: no request_id returned")

    result = await poll_request(request_id, max_wait_sec=120)

    status = result.get("status", "").lower()
    if status in ("success", "completed", "succeeded"):
        urls = extract_media_urls(result)
        if urls:
            return ImageGenResult(success=True, image_url=urls[0], status="completed")
        return ImageGenResult(success=False, status="blocked", error="ImageEdit: no media URLs")
    else:
        error = result.get("error", "Unknown error")
        return ImageGenResult(success=False, status="blocked", error=f"ImageEdit: {error}")
