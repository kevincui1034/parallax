"""Receive images node — validate and store uploaded images (multi-image support)."""

import hashlib
import logging
import os

from ..config import load_settings

logger = logging.getLogger(__name__)


async def receive_image(state: dict) -> dict:
    """Validate and store uploaded images."""
    settings = load_settings()
    input_images = state.get("input_images", [])

    if not input_images:
        state["status"] = "blocked"
        state["simple_message"] = "No images provided."
        return state

    os.makedirs(settings.upload_dir, exist_ok=True)

    # Process each image
    processed = []
    for i, img in enumerate(input_images):
        img_id = f"image_{i+1:03d}"
        local_path = img.get("url", "")

        # Compute hash
        try:
            clean_path = local_path.replace("file://", "")
            if os.path.exists(clean_path):
                with open(clean_path, "rb") as f:
                    photo_hash = hashlib.md5(f.read()).hexdigest()
            else:
                photo_hash = hashlib.md5(local_path.encode()).hexdigest()
        except Exception:
            photo_hash = "unknown"

        processed.append({
            "id": img_id,
            "url": local_path,
            "public_url": img.get("public_url", local_path),
            "role": img.get("role", "main object photo"),
            "hash": photo_hash,
        })

    state["input_images"] = processed
    state["status"] = "understanding"
    state["progress"] = 10
    state["simple_message"] = f"{len(processed)} image(s) received. Analyzing object..."

    logger.info("receive_images: %d images received", len(processed))
    return state
