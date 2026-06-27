"""Shared pipeline execution + persistence.

Imported by both the HTTP API (main.py) and the MCP server (mcp_server.py) so
there is a single source of truth for running the manual pipeline and saving the
resulting job state.
"""

import logging

from .graph import run_pipeline
from .jobs import update_job

logger = logging.getLogger(__name__)


async def run_and_persist(job_id: str, input_images: list[dict], goal: str | None = None) -> None:
    """Run the full pipeline for a job and persist the final state."""
    initial_state = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "simple_message": "Job queued.",
        "input_images": input_images,
        "user_prompt": goal or "",
    }
    try:
        final_state = await run_pipeline(initial_state)
        update_job(
            job_id,
            status=final_state.get("status", "blocked"),
            progress=final_state.get("progress", 100),
            simple_message=final_state.get("simple_message", ""),
            input_images=final_state.get("input_images", []),
            object_type=final_state.get("object_type", ""),
            likely_model=final_state.get("likely_model", ""),
            object_confidence=final_state.get("object_confidence", 0.0),
            object_summary=final_state.get("object_summary", ""),
            parts=final_state.get("parts", []),
            sections=final_state.get("sections", []),
            steps=final_state.get("steps", []),
            visual_overlay=final_state.get("visual_overlay", {}),
            kling_prompts=final_state.get("kling_prompts", {}),
            warnings=final_state.get("warnings", []),
            non_claims=final_state.get("non_claims", []),
            citations=final_state.get("citations", []),
            search_queries=final_state.get("search_queries", []),
            search_results=final_state.get("search_results", []),
            explode=final_state.get("explode", {}),
            turntable=final_state.get("turntable", {}),
            manual_json=final_state.get("manual_json", {}),
            artifact_dir=final_state.get("artifact_dir", ""),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("pipeline error for job %s: %s", job_id, e)
        update_job(job_id, status="blocked", progress=100, simple_message=f"Pipeline error: {e}")
