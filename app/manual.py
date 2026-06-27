"""Manual JSON schema builder.

manual.json is the source of truth. PDF and HTML are render targets.
"""

import json
import logging
import os
import time

from .config import load_settings

logger = logging.getLogger(__name__)


def build_manual_json(state: dict) -> dict:
    """Build the canonical manual.json from pipeline state.

    This is the structured artifact that agents consume through MCP.
    """
    job_id = state.get("job_id", "")
    parts = state.get("parts", [])
    citations = state.get("citations", [])
    explode = state.get("explode", {})
    turntable = state.get("turntable", {})
    input_images = state.get("input_images", [])

    # Assign numbers to parts
    numbered_parts = []
    for i, p in enumerate(parts):
        p_copy = dict(p)
        p_copy["number"] = i + 1
        numbered_parts.append(p_copy)

    # Build sections
    sections = [
        {
            "id": "overview",
            "title": "Object Overview",
        },
        {
            "id": "layout",
            "title": "Numbered Layout",
            "parts": [p["id"] for p in numbered_parts],
        },
        {
            "id": "exploded",
            "title": "Exploded View",
            "media": {
                "video": explode.get("video_url", ""),
                "frames": explode.get("frames", []),
                "status": explode.get("status", "blocked"),
            },
        },
        {
            "id": "turntable",
            "title": "360-Style Preview",
            "media": {
                "video": turntable.get("video_url", ""),
                "frames": turntable.get("frames", []),
                "status": turntable.get("status", "blocked"),
            },
        },
        {
            "id": "parts",
            "title": "Part Cards",
            "parts": [p["id"] for p in numbered_parts],
        },
        {
            "id": "operations",
            "title": "Operation Pages",
        },
        {
            "id": "warnings",
            "title": "Warnings and Unknowns",
        },
        {
            "id": "sources",
            "title": "Sources and Confidence Report",
        },
    ]

    manual = {
        "schema_version": 1,
        "job_id": job_id,
        "title": "Generated Visual Manual",
        "status": state.get("status", "unknown"),
        "generated_at": time.time(),
        "object": {
            "type": state.get("object_type", "unknown"),
            "likely_name": state.get("object_type", "unknown"),
            "likely_model": state.get("likely_model", "unknown"),
            "confidence": state.get("object_confidence", 0.0),
            "summary": state.get("object_summary", ""),
        },
        "input_images": [
            {
                "id": img.get("id", f"image_{i+1:03d}"),
                "url": img.get("url", ""),
                "role": img.get("role", "main object photo"),
            }
            for i, img in enumerate(input_images)
        ],
        "parts": numbered_parts,
        "sections": sections,
        "steps": state.get("steps", []),
        "visual_overlay": state.get("visual_overlay", {"labels": []}),
        "warnings": state.get("warnings", [
            "This is an AI-generated visual guide, not manufacturer-certified documentation.",
        ]),
        "non_claims": state.get("non_claims", [
            "Not a true 3D model.",
            "Not repair certification.",
            "Not an electrical safety approval.",
        ]),
        "citations": citations,
        "search_queries": state.get("search_queries", []),
        "kling_prompts": state.get("kling_prompts", {}),
        "artifacts": {
            "html_url": "",
            "pdf_url": "",
            "exploded_frames_url": "",
            "turntable_frames_url": "",
        },
        "snaplii_actions": state.get("snaplii_actions", []),
    }

    return manual


def save_manual_json(state: dict, artifact_dir: str) -> str:
    """Build and save manual.json to the artifact directory.

    Returns the file path.
    """
    manual = build_manual_json(state)
    guide_dir = os.path.join(artifact_dir, "guide")
    os.makedirs(guide_dir, exist_ok=True)

    manual_path = os.path.join(guide_dir, "manual.json")
    with open(manual_path, "w", encoding="utf-8") as f:
        json.dump(manual, f, indent=2)

    # Also save parts.json
    parts_path = os.path.join(guide_dir, "parts.json")
    with open(parts_path, "w", encoding="utf-8") as f:
        json.dump(manual["parts"], f, indent=2)

    # Save citations.json
    citations_path = os.path.join(guide_dir, "citations.json")
    with open(citations_path, "w", encoding="utf-8") as f:
        json.dump(manual["citations"], f, indent=2)

    # Save overlay.json (for SVG overlay labels)
    overlay = state.get("visual_overlay", {"labels": []})
    if not overlay.get("labels"):
        # Generate from parts if not provided by Gemini
        labels = []
        for p in manual["parts"]:
            labels.append({
                "part_id": p["id"],
                "number": p["number"],
                "label": p["label"],
                "anchor": [0.5, 0.5],
                "label_position": [0.7, 0.3],
            })
        overlay = {"labels": labels}
    overlay_path = os.path.join(guide_dir, "overlay.json")
    with open(overlay_path, "w", encoding="utf-8") as f:
        json.dump(overlay, f, indent=2)

    logger.info("save_manual_json: written to %s", manual_path)
    return manual_path
