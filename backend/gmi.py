"""
GMI Cloud client — everything is routed through GMI.

Two API surfaces (confirmed against docs.gmicloud.ai):
  1. Chat (3.5 Flash): OpenAI-compatible  POST {GMI_BASE_URL}/chat/completions
  2. Image (GPT-Image-2-Edit) + Video (Kling V3): async request-queue
       POST {GMI_REQUEST_QUEUE_URL}/requests        {model, payload} -> {request_id}
       GET  {GMI_REQUEST_QUEUE_URL}/requests/{id}    -> {status, outcome.media_urls[].url}
     status flows queued -> processing -> success (or failed/error).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional

import httpx
from dotenv import load_dotenv

# Load .env before reading config below (decoupled from import order in main).
load_dotenv()

GMI_API_KEY = os.getenv("GMI_API_KEY", "")
GMI_BASE_URL = os.getenv("GMI_BASE_URL", "https://api.gmi-serving.com/v1")
GMI_REQUEST_QUEUE_URL = os.getenv(
    "GMI_REQUEST_QUEUE_URL",
    "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey",
)

CHAT_MODEL = os.getenv("GMI_CHAT_MODEL", "google/gemini-3.5-flash")
IMAGE_MODEL = os.getenv("GMI_IMAGE_MODEL", "gpt-image-2-edit")
VIDEO_MODEL = os.getenv("GMI_VIDEO_MODEL", "kling-v3-image-to-video")

ProgressCb = Optional[Callable[[str], Awaitable[None] | None]]


def is_configured() -> bool:
    """True when a GMI key is present — callers fall back to stubs otherwise."""
    return bool(GMI_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GMI_API_KEY}",
        "Content-Type": "application/json",
    }


class GMIError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Chat — OpenAI-compatible
# --------------------------------------------------------------------------- #
async def chat(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> str:
    """Call the chat model and return the assistant message content."""
    body: dict[str, Any] = {
        "model": model or CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GMI_BASE_URL}/chat/completions", json=body, headers=_headers()
        )
        if r.status_code >= 400:
            raise GMIError(f"chat {r.status_code}: {r.text[:300]}")
        data = r.json()
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Request-queue — media models (image, video)
# --------------------------------------------------------------------------- #
async def _submit(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GMI_REQUEST_QUEUE_URL}/requests",
            json={"model": model, "payload": payload},
            headers=_headers(),
        )
        if r.status_code >= 400:
            raise GMIError(f"submit {model} {r.status_code}: {r.text[:300]}")
        return r.json()


async def _poll(request_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"{GMI_REQUEST_QUEUE_URL}/requests/{request_id}", headers=_headers()
        )
        if r.status_code >= 400:
            raise GMIError(f"poll {request_id} {r.status_code}: {r.text[:300]}")
        return r.json()


def _media_urls(resp: dict[str, Any]) -> list[str]:
    outcome = resp.get("outcome") or {}
    urls = outcome.get("media_urls") or []
    # entries look like {"id": "0", "url": "https://..."}
    return [u["url"] for u in urls if isinstance(u, dict) and u.get("url")]


async def run_media(
    model: str,
    payload: dict[str, Any],
    *,
    timeout_s: float = 300,
    interval_s: float = 3.0,
    on_status: ProgressCb = None,
) -> list[str]:
    """Submit a media request and poll until it succeeds; return media URLs."""
    submitted = await _submit(model, payload)
    # Some responses already carry a terminal status + outcome.
    if submitted.get("status") == "success":
        return _media_urls(submitted)
    request_id = submitted.get("request_id") or submitted.get("id")
    if not request_id:
        raise GMIError(f"{model}: no request_id in {submitted}")

    waited = 0.0
    while waited < timeout_s:
        await asyncio.sleep(interval_s)
        waited += interval_s
        resp = await _poll(request_id)
        status = (resp.get("status") or "").lower()
        if on_status:
            res = on_status(status)
            if asyncio.iscoroutine(res):
                await res
        if status == "success":
            return _media_urls(resp)
        if status in {"failed", "error", "cancelled"}:
            raise GMIError(f"{model} {status}: {resp.get('error') or resp}")
    raise GMIError(f"{model}: timed out after {timeout_s}s")


# --------------------------------------------------------------------------- #
# High-level pipeline helpers
# --------------------------------------------------------------------------- #
async def generate_part_images(
    prompt: str,
    *,
    image_b64: Optional[str] = None,
    n: int = 1,
    size: str = "1024x1024",
    on_status: ProgressCb = None,
) -> list[str]:
    """
    GPT-Image-2-Edit: generate the part image / multi-angle shots.
    `image_b64` is the user's uploaded photo (edit/reference input).
    """
    payload: dict[str, Any] = {"prompt": prompt, "n": n, "size": size}
    if image_b64:
        payload["image"] = image_b64
    return await run_media(IMAGE_MODEL, payload, on_status=on_status)


async def image_to_video(
    image: str,
    prompt: str,
    *,
    duration: int = 5,
    image_tail: Optional[str] = None,
    on_status: ProgressCb = None,
) -> str:
    """Kling V3: turn a part image into an exploded-view clip; return its URL."""
    payload: dict[str, Any] = {
        "image": image,
        "prompt": prompt,
        "duration": duration,
        "sound": "off",
    }
    if image_tail:
        payload["image_tail"] = image_tail
    urls = await run_media(VIDEO_MODEL, payload, on_status=on_status)
    if not urls:
        raise GMIError("Kling returned no media_urls")
    return urls[0]
