"""Frame extraction from video using ffmpeg.

Downloads a video URL, extracts N frames evenly, and returns frame URLs.
"""

import asyncio
import logging
import os
import tempfile
import base64

from .config import load_settings

logger = logging.getLogger(__name__)


async def extract_frames(video_url: str, count: int = 24) -> list[str]:
    """Download video and extract frames using ffmpeg.

    Returns list of data URIs (base64-encoded JPEGs) for frontend display.
    """
    settings = load_settings()

    try:
        import httpx
        import subprocess
    except ImportError:
        logger.warning("extract_frames: httpx not available")
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        # Download video
        video_path = os.path.join(tmpdir, "input.mp4")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    f.write(resp.content)
        except Exception as e:
            logger.warning("extract_frames: failed to download video — %s", e)
            return []

        # Check ffmpeg is available
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                logger.warning("extract_frames: ffmpeg not available")
                return []
        except FileNotFoundError:
            logger.warning("extract_frames: ffmpeg not installed")
            return []

        # Extract frames
        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        # Use fps filter to extract N frames evenly
        # First get video duration
        duration_proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await duration_proc.communicate()
        try:
            duration = float(stdout.decode().strip())
        except (ValueError, TypeError):
            duration = 5.0

        fps = count / duration if duration > 0 else count / 5.0

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps={fps:.2f},scale=640:-1",
            "-q:v", "2",
            "-frames:v", str(count),
            os.path.join(frames_dir, "frame_%03d.jpg"),
            "-y",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Read frames and convert to data URIs
        frame_files = sorted(
            f for f in os.listdir(frames_dir) if f.endswith(".jpg")
        )

        frames = []
        for fname in frame_files[:count]:
            with open(os.path.join(frames_dir, fname), "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
                frames.append(f"data:image/jpeg;base64,{encoded}")

        logger.info("extract_frames: extracted %d frames from video", len(frames))
        return frames
