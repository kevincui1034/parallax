"""Generate video node — submit Kling image-to-video for explode + turntable."""

import asyncio
import logging

from ..adapters.kling import generate_video
from ..adapters.fallback import generate_placeholder_frames
from ..config import load_settings
from ..frames import extract_frames

logger = logging.getLogger(__name__)


async def generate_videos(state: dict) -> dict:
    """Generate explode and turntable videos via Kling, then extract frames."""
    settings = load_settings()
    image_url = ""
    input_images = state.get("input_images", [])
    if input_images:
        image_url = input_images[0].get("public_url", input_images[0].get("url", ""))
    kling_prompts = state.get("kling_prompts", {})

    state["status"] = "generating"
    state["progress"] = 45
    state["simple_message"] = "Generating visual animations..."

    # Generate both videos concurrently
    explode_task = generate_video(
        image_url,
        kling_prompts.get("explode", "High-fidelity industrial product animation using 3D Spacetime physics. From the starting frame, the mechanical assembly smoothly breaks apart into a perfectly aligned exploded view. The outer shell casing panels slide directly outwards. Internal components float gracefully in place with true physical weight, inertia, and zero distortion. Cinematic slow push-in with subtle 30-degree orbital pan. Photorealistic shadows and studio lighting. No debris, no text, no labels, no logos."),
        mode="explode",
    )
    turntable_task = generate_video(
        image_url,
        kling_prompts.get("turntable", "Smooth cinematic 360-degree product turntable rotation on a clean neutral studio background. Photorealistic lighting and reflections update naturally as the object rotates. High engineering commercial aesthetic, no text, no labels, no logos."),
        mode="turntable",
    )

    explode_result, turntable_result = await asyncio.gather(explode_task, turntable_task)

    # Process explode result
    explode_data = {"mode": "explode", "status": explode_result.status, "video_url": "", "frames": [], "error": explode_result.error}
    if explode_result.success and explode_result.video_url:
        explode_data["video_url"] = explode_result.video_url
        state["status"] = "extracting"
        state["simple_message"] = "Extracting frames from explode animation..."
        frames = await extract_frames(explode_result.video_url, count=settings.frame_count)
        if frames:
            explode_data["frames"] = frames
            explode_data["status"] = "completed"
        else:
            # Video generated but frame extraction failed — use video URL directly
            explode_data["status"] = "completed"
            logger.warning("generate_videos: frame extraction failed for explode — using video URL only")
    else:
        logger.warning("generate_videos: explode blocked — %s", explode_result.error)
        explode_data["frames"] = generate_placeholder_frames(settings.frame_count, "explode", image_url)
        explode_data["status"] = "blocked"

    # Process turntable result
    turntable_data = {"mode": "turntable", "status": turntable_result.status, "video_url": "", "frames": [], "error": turntable_result.error}
    if turntable_result.success and turntable_result.video_url:
        turntable_data["video_url"] = turntable_result.video_url
        frames = await extract_frames(turntable_result.video_url, count=settings.frame_count)
        if frames:
            turntable_data["frames"] = frames
            turntable_data["status"] = "completed"
        else:
            turntable_data["status"] = "completed"
            logger.warning("generate_videos: frame extraction failed for turntable — using video URL only")
    else:
        logger.warning("generate_videos: turntable blocked — %s", turntable_result.error)
        turntable_data["frames"] = generate_placeholder_frames(settings.frame_count, "turntable", image_url)
        turntable_data["status"] = "blocked"

    state["explode"] = explode_data
    state["turntable"] = turntable_data

    # Determine overall status
    both_blocked = explode_data["status"] == "blocked" and turntable_data["status"] == "blocked"
    if both_blocked:
        state["status"] = "partial"
        state["simple_message"] = "Video generation blocked. Using placeholder frames for slider."
    else:
        state["status"] = "rendering"
        state["simple_message"] = "Videos generated. Building visual manual..."

    state["progress"] = 65
    return state
