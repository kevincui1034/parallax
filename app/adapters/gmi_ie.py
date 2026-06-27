"""GMI Cloud request-queue adapter.

Shared logic for submitting/polling async inference requests
(image generation, video generation) via the GMI Cloud API.

Flow:
1. POST /requests with {model, payload} → get request_id
2. GET /requests/{request_id} → poll until status=success/failed
3. Extract output URLs from outcome.media_urls
"""

import asyncio
import logging
from typing import Optional

import httpx

from ..config import load_settings

logger = logging.getLogger(__name__)


async def submit_request(model: str, payload: dict) -> dict:
    """Submit a generation request to GMI Cloud.

    Returns the response JSON (includes request_id or error).
    """
    settings = load_settings()
    url = f"{settings.gmi_ie_base_url}/requests"

    headers = {
        "Authorization": f"Bearer {settings.gmi_api_key}",
        "Content-Type": "application/json",
    }

    body = {"model": model, "payload": payload}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=headers)
        data = resp.json()

        if resp.status_code >= 400:
            error = data.get("error", data.get("message", f"HTTP {resp.status_code}"))
            logger.warning("GMI IE: submit failed for %s — %s", model, error)
            return {"error": error}

        return data


async def poll_request(request_id: str, max_wait_sec: int = 300, interval: int = 5) -> dict:
    """Poll a GMI Cloud request until completion.

    Returns the final response JSON with outcome.media_urls on success.
    """
    settings = load_settings()
    url = f"{settings.gmi_ie_base_url}/requests/{request_id}"

    headers = {
        "Authorization": f"Bearer {settings.gmi_api_key}",
    }

    elapsed = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while elapsed < max_wait_sec:
            await asyncio.sleep(interval)
            elapsed += interval

            try:
                resp = await client.get(url, headers=headers)
                data = resp.json()
            except Exception as e:
                logger.warning("GMI IE: poll error — %s", e)
                continue

            status = data.get("status", "").lower()

            if status in ("success", "completed", "succeeded"):
                logger.info("GMI IE: request %s completed", request_id)
                return data

            if status in ("failed", "error", "cancelled"):
                error = data.get("error", data.get("message", "Unknown error"))
                logger.warning("GMI IE: request %s failed — %s", request_id, error)
                return {"status": "failed", "error": error}

            logger.debug("GMI IE: polling %s... status=%s, elapsed=%ds", request_id, status, elapsed)

    logger.warning("GMI IE: polling timed out after %ds", max_wait_sec)
    return {"status": "timeout", "error": f"Polling timed out after {max_wait_sec}s"}


def extract_media_urls(result: dict) -> list[str]:
    """Extract media URLs from a completed GMI IE response."""
    outcome = result.get("outcome", {})
    urls = []
    for media in outcome.get("media_urls", []):
        url = media.get("url", "")
        if url:
            urls.append(url)
    return urls


async def upload_file(file_path: str, file_type: str = "png") -> Optional[str]:
    """Upload a file to GMI Cloud Inference Storage and return the public URL.

    Uses the two-step upload API:
    1. GET /upload-url to get a presigned upload URL + public URL
    2. PUT raw bytes to the presigned URL
    """
    settings = load_settings()
    base = settings.gmi_ie_base_url.replace("/apikey", "/apikey")

    headers = {
        "Authorization": f"Bearer {settings.gmi_api_key}",
    }

    import os
    file_name = os.path.basename(file_path)

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Step 1: Get upload URL
        resp = await client.get(
            f"{settings.gmi_ie_base_url}/upload-url",
            params={"file_type": file_type, "file_name": file_name},
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning("GMI IE: upload-url failed — %s", resp.text[:200])
            return None

        data = resp.json()
        upload_url = data.get("upload_url", "")
        public_url = data.get("public_url", "")

        if not upload_url or not public_url:
            logger.warning("GMI IE: upload-url response missing URLs")
            return None

        # Step 2: PUT file bytes to upload_url
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        content_type = f"image/{file_type}" if file_type in ("png", "jpeg", "jpg") else f"video/{file_type}"
        put_resp = await client.put(upload_url, content=file_bytes, headers={"Content-Type": content_type})
        if put_resp.status_code >= 400:
            logger.warning("GMI IE: file upload failed — %d", put_resp.status_code)
            return None

        logger.info("GMI IE: file uploaded, public_url=%s", public_url)
        return public_url
