"""Fallback adapter — provides placeholder frames when video generation is blocked.

Generates static placeholder frames so the frontend slider still works.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

# Placeholder frame data — small SVG-based frames rendered as data URIs
# In production, these would be extracted from generated video.


def generate_placeholder_frames(count: int = 24, mode: str = "turntable") -> list[str]:
    """Generate placeholder frame URLs (data URIs) for the slider.

    Creates simple gradient frames that shift hue to simulate rotation.
    """
    frames = []
    for i in range(count):
        progress = i / max(count - 1, 1)
        # Shift hue across frames to simulate rotation
        hue = int(progress * 360)
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
<defs>
<linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
<stop offset="0%" style="stop-color:hsl({hue},40%,80%)"/>
<stop offset="100%" style="stop-color:hsl({hue + 60},40%,60%)"/>
</linearGradient>
</defs>
<rect width="640" height="480" fill="url(#g)"/>
<circle cx="320" cy="240" r="120" fill="rgba(255,255,255,0.3)" stroke="rgba(0,0,0,0.2)" stroke-width="2"/>
<text x="320" y="250" text-anchor="middle" font-family="sans-serif" font-size="16" fill="rgba(0,0,0,0.5)">
{mode} — frame {i + 1}/{count}
</text>
</svg>"""
        import base64
        encoded = base64.b64encode(svg.encode()).decode()
        frames.append(f"data:image/svg+xml;base64,{encoded}")

    logger.info("Fallback: generated %d placeholder frames for %s", count, mode)
    return frames
