"""Analyze object node — Gemini 3.5 Flash analyzes images and produces structured JSON.

Output: object_type, likely_model, object_confidence, object_summary, parts, kling_prompts, warnings, non_claims
"""

import json
import logging

from ..llm import gmi_vision, parse_json_response

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are an expert product analyst. Analyze this image and create a structured visual manual plan.

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:

{
  "object_type": "short object type name",
  "likely_model": "specific model name if identifiable, or 'unknown'",
  "object_confidence": 0.0-1.0,
  "object_summary": "one sentence description of what the object is",
  "likely_parts": [
    {
      "id": "snake_case_id",
      "label": "Human-readable label",
      "function": "what this part does in one sentence",
      "confidence": 0.0-1.0,
      "description": "longer description of the part",
      "visual_evidence": "what visual feature identifies it",
      "source_status": "vision_inferred",
      "unknowns": ["things that cannot be determined from image alone"],
      "warnings": ["any safety warning specific to this part"]
    }
  ],
  "visual_overlay": {
    "labels": [
      {
        "part_id": "snake_case_id matching a part above",
        "number": 1,
        "label": "short label for the overlay",
        "anchor": [0.0-1.0, 0.0-1.0],
        "label_position": [0.0-1.0, 0.0-1.0]
      }
    ]
  },
  "steps": [
    {
      "id": "step_001",
      "title": "short step title",
      "instruction": "one sentence instruction",
      "part_ids": ["relevant part ids"],
      "confidence": 0.0-1.0
    }
  ],
  "kling_prompts": {
    "explode": "Prompt for Kling image-to-video to create an exploded-view animation. Keep object centered. Separate visible components with smooth motion. Do not add text, labels, logos, or people.",
    "turntable": "Prompt for Kling image-to-video to create a slow 360-style product turntable preview. Keep object centered on neutral background. Do not add text or labels."
  },
  "warnings": ["This is an AI-generated visual guide, not manufacturer-certified documentation."],
  "non_claims": ["Not a true 3D model.", "Not repair certification.", "Not an electrical safety approval."]
}

Rules:
- Identify 3-8 likely visible parts based on what you can see.
- Be honest about confidence — do not invent parts you cannot see.
- Do not claim exact pinouts, specs, or safety information.
- Mark anything you cannot verify as unknown.
- For visual_overlay anchors, use normalized coordinates where [0.5, 0.5] is the center of the image.
- Keep prompts concise and focused on visual motion.
- Include 2-4 basic steps for using/orienting the object."""


async def analyze_object(state: dict) -> dict:
    """Use Gemini to analyze the image and produce a structured part plan."""
    input_images = state.get("input_images", [])
    if not input_images:
        state["status"] = "blocked"
        state["simple_message"] = "No images to analyze."
        return state

    # Use first image for primary analysis — prefer local file path for base64 conversion
    image_url = input_images[0].get("url", input_images[0].get("public_url", ""))

    state["status"] = "understanding"
    state["progress"] = 20
    state["simple_message"] = "Analyzing object with Gemini..."

    raw = await gmi_vision(image_url, ANALYSIS_PROMPT)

    if not raw:
        logger.warning("analyze_object: VLM returned empty — using defaults")
        state["object_type"] = "unknown object"
        state["likely_model"] = "unknown"
        state["object_confidence"] = 0.3
        state["object_summary"] = "An object from the uploaded photo."
        state["parts"] = [
            {"id": "main_body", "label": "Main body", "function": "Primary structure of the object", "confidence": 0.5, "description": "Primary structure", "visual_evidence": "Central visible component", "source_status": "vision_inferred", "unknowns": ["Exact function cannot be determined from image alone"], "warnings": []}
        ]
        state["visual_overlay"] = {"labels": [{"part_id": "main_body", "number": 1, "label": "Main body", "anchor": [0.5, 0.5], "label_position": [0.7, 0.3]}]}
        state["steps"] = [{"id": "step_001", "title": "Orient the object", "instruction": "Place the object so the main visible face matches the annotated image.", "part_ids": [], "confidence": 0.8}]
        state["kling_prompts"] = {
            "explode": "Create a clean technical exploded-view animation of this product photo. Keep the object centered. Separate visible components slightly with smooth motion. Do not add text, labels, logos, extra parts, or people.",
            "turntable": "Create a slow 360-style product turntable preview from this product photo. Keep the object centered and stable on a neutral background. Do not add text, labels, or new components."
        }
        state["warnings"] = ["This is an AI-generated visual guide, not manufacturer-certified documentation."]
        state["non_claims"] = ["Not a true 3D model.", "Not repair certification.", "Not an electrical safety approval."]
    else:
        parsed = parse_json_response(raw)
        if parsed:
            state["object_type"] = parsed.get("object_type", "unknown object")
            state["likely_model"] = parsed.get("likely_model", "unknown")
            state["object_confidence"] = parsed.get("object_confidence", 0.5)
            state["object_summary"] = parsed.get("object_summary", "")
            state["parts"] = parsed.get("likely_parts", [])
            state["visual_overlay"] = parsed.get("visual_overlay", {"labels": []})
            state["steps"] = parsed.get("steps", [])
            state["kling_prompts"] = parsed.get("kling_prompts", {})
            state["warnings"] = parsed.get("warnings", ["This is an AI-generated visual guide, not manufacturer-certified documentation."])
            state["non_claims"] = parsed.get("non_claims", ["Not a true 3D model.", "Not repair certification.", "Not an electrical safety approval."])
            logger.info("analyze_object: %s (model=%s) — %d parts identified", state["object_type"], state["likely_model"], len(state["parts"]))
        else:
            logger.warning("analyze_object: could not parse JSON — using defaults")
            state["object_type"] = "unknown object"
            state["likely_model"] = "unknown"
            state["object_confidence"] = 0.3
            state["object_summary"] = "An object from the uploaded photo."
            state["parts"] = [{"id": "main_body", "label": "Main body", "function": "Primary structure", "confidence": 0.5, "description": "Primary structure", "visual_evidence": "Central visible component", "source_status": "vision_inferred", "unknowns": [], "warnings": []}]
            state["visual_overlay"] = {"labels": [{"part_id": "main_body", "number": 1, "label": "Main body", "anchor": [0.5, 0.5], "label_position": [0.7, 0.3]}]}
            state["steps"] = [{"id": "step_001", "title": "Orient the object", "instruction": "Place the object so the main visible face matches the annotated image.", "part_ids": [], "confidence": 0.8}]
            state["kling_prompts"] = {
                "explode": "Create a clean technical exploded-view animation of this product photo. Keep the object centered. Separate visible components slightly with smooth motion. Do not add text, labels, logos, extra parts, or people.",
                "turntable": "Create a slow 360-style product turntable preview from this product photo. Keep the object centered and stable on a neutral background. Do not add text, labels, or new components."
            }
            state["warnings"] = ["This is an AI-generated visual guide, not manufacturer-certified documentation."]
            state["non_claims"] = ["Not a true 3D model.", "Not repair certification.", "Not an electrical safety approval."]

    state["status"] = "searching"
    state["progress"] = 30
    state["simple_message"] = f"Object analyzed: {state['object_type']}. {len(state['parts'])} parts identified."
    return state
