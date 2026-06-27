"""Complete job node — set final status."""

import logging

logger = logging.getLogger(__name__)


async def complete_job(state: dict) -> dict:
    """Set the final job status based on results."""
    explode = state.get("explode", {})
    turntable = state.get("turntable", {})
    parts = state.get("parts", [])
    artifact_dir = state.get("artifact_dir", "")
    manual_json = state.get("manual_json", {})

    explode_ok = explode.get("status") == "completed"
    turntable_ok = turntable.get("status") == "completed"
    has_parts = len(parts) > 0
    has_artifact = bool(artifact_dir) and bool(manual_json)

    if has_parts and has_artifact and (explode_ok or turntable_ok):
        state["status"] = "completed"
        state["progress"] = 100
        state["simple_message"] = "Visual manual ready. PDF and interactive HTML generated."
    elif has_parts and has_artifact:
        state["status"] = "partial"
        state["progress"] = 100
        state["simple_message"] = "Visual manual ready (video generation was blocked, using placeholder frames). PDF and HTML generated."
    else:
        state["status"] = "blocked"
        state["progress"] = 100
        state["simple_message"] = "Could not generate visual manual."

    logger.info("complete_job: status=%s, parts=%d, artifact_dir=%s", state["status"], len(parts), bool(artifact_dir))
    return state
