"""Storage adapter — local filesystem (dev) or Vercel Blob (serverless prod).

On Vercel, the filesystem is ephemeral and not shared across invocations, so all
durable artifacts (job state, source images, PDFs) are written to Vercel Blob and
served via their public URLs. Locally, we fall back to the upload_dir on disk and
serve via the /uploads static mount.

Mode is selected by the presence of BLOB_READ_WRITE_TOKEN.
"""

import json
import logging
import os
from typing import Optional

import httpx

from .config import load_settings

logger = logging.getLogger(__name__)

_BLOB_API = "https://blob.vercel-storage.com"
_API_VERSION = "7"

# Cached blob store base URL (e.g. https://<store>.public.blob.vercel-storage.com)
_blob_base: Optional[str] = None


def blob_token() -> str:
    return os.environ.get("BLOB_READ_WRITE_TOKEN", "")


def is_blob() -> bool:
    return bool(blob_token())


def _local_path(key: str) -> str:
    settings = load_settings()
    path = os.path.join(settings.upload_dir, key.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _local_url(key: str) -> str:
    # Served by the /uploads static mount or absolute base if configured.
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/uploads/{key}" if base else f"/uploads/{key}"


async def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Store bytes at `key`. Returns a public URL."""
    if is_blob():
        global _blob_base
        headers = {
            "authorization": f"Bearer {blob_token()}",
            "x-api-version": _API_VERSION,
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(f"{_BLOB_API}/{key}", content=data, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            url = body.get("url", "")
            if url:
                # Cache the store base for list-free reads.
                try:
                    _blob_base = url.rsplit("/", 1)[0].rsplit("/" + key.split("/")[0], 1)[0]
                except Exception:
                    pass
            logger.info("storage.put_bytes: blob %s -> %s", key, url)
            return url

    path = _local_path(key)
    with open(path, "wb") as f:
        f.write(data)
    return _local_url(key)


async def put_text(key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> str:
    return await put_bytes(key, text.encode("utf-8"), content_type)


async def put_json(key: str, obj: dict) -> str:
    return await put_bytes(key, json.dumps(obj).encode("utf-8"), "application/json")


async def _blob_url_for(key: str) -> Optional[str]:
    """Resolve the public URL for a blob key via env base or the list API."""
    base = os.environ.get("BLOB_BASE_URL", "").rstrip("/") or _blob_base
    if base:
        return f"{base}/{key}"
    # Fall back to listing by prefix.
    headers = {"authorization": f"Bearer {blob_token()}", "x-api-version": _API_VERSION}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_BLOB_API, params={"prefix": key, "limit": "1"}, headers=headers)
        if resp.status_code >= 400:
            return None
        blobs = resp.json().get("blobs", [])
        for b in blobs:
            if b.get("pathname") == key:
                return b.get("url")
        return blobs[0].get("url") if blobs else None


async def get_bytes(key: str) -> Optional[bytes]:
    """Read bytes for `key`. Returns None if missing."""
    if is_blob():
        url = await _blob_url_for(key)
        if not url:
            return None
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            return resp.content
    path = _local_path(key)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


async def get_bytes_from_url(url: str) -> Optional[bytes]:
    """Fetch bytes from any http(s) URL (e.g. a Blob public URL)."""
    if not url.startswith("http"):
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "image/*,*/*",
    }
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            logger.warning("storage.get_bytes_from_url: %s -> %d", url, resp.status_code)
            return None
        return resp.content


async def get_text(key: str) -> Optional[str]:
    data = await get_bytes(key)
    return data.decode("utf-8") if data is not None else None


async def get_json(key: str) -> Optional[dict]:
    text = await get_text(key)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
