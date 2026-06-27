"""FastAPI application for Agent Visual Manual.

Routes:
- GET  /health
- GET  /credentials
- POST /v1/manuals (multi-image upload)
- GET  /v1/manuals/{job_id}
- GET  /v1/manuals/{job_id}/events (SSE)
- GET  /v1/manuals/{job_id}/artifacts (aggregated)
- GET  /v1/manuals/{job_id}/artifacts/manual.pdf
- GET  /v1/manuals/{job_id}/artifacts/index.html
- GET  /v1/manuals/{job_id}/artifacts/manual.json
- GET  /v1/manuals/{job_id}/parts/{part_id}
- POST /v1/manuals/{job_id}/ask
- POST /v1/manuals/{job_id}/export
- POST /v1/manuals/{job_id}/validate
- MCP: GET  /mcp/resources
- MCP: GET  /mcp/resources/{uri:path}
- MCP: POST /mcp/tools/{tool_name}
- MCP: GET  /mcp/prompts
- GET  /stub (HTML frontend)
- GET  / (redirect to stub)
"""

import asyncio
import json
import logging
import os
import time
from typing import Annotated

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings, credential_report
from .jobs import create_job, get_job, update_job, _store
from .tracing import setup_tracing
from .graph import run_pipeline
from .llm import gmi_chat
from . import storage
from .nodes.build_artifact import _build_manual_html
from .pdf import render_pdf
from .pipeline_runner import run_and_persist
from .mcp_server import router as mcp_router
from .adapters import snaplii as snaplii_adapter
import tempfile

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

setup_tracing()

app = FastAPI(title="Agent Visual Manual", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Real MCP server (JSON-RPC 2.0 over Streamable HTTP) for external agents.
app.include_router(mcp_router)

# Serve uploaded images and artifact files statically (local/dev only).
_settings = load_settings()
if not storage.is_blob():
    try:
        os.makedirs(_settings.upload_dir, exist_ok=True)
        app.mount("/uploads", StaticFiles(directory=_settings.upload_dir), name="uploads")
    except Exception as e:
        logger.warning("uploads mount skipped: %s", e)

# ─── Helpers ──────────────────────────────────────────────────────────────

_STATUS_TO_EVENT = {
    "queued": "image_uploaded",
    "understanding": "understanding_image",
    "searching": "searching_context",
    "planning": "planning_parts",
    "generating": "generating_visual",
    "generating_exploded_video": "generating_exploded_video",
    "extracting": "extracting_frames",
    "rendering": "rendering_html",
    "rendering_pdf": "rendering_pdf",
    "validating": "validating_artifact",
    "completed": "completed",
    "partial": "partial",
    "blocked": "blocked",
}


def _status_to_event(status: str) -> str:
    return _STATUS_TO_EVENT.get(status, status)


def _read_artifact_file(job_id: str, *subpath: str) -> str | None:
    """Read a file from the artifact directory. Returns None if not found."""
    job = get_job(job_id)
    if not job or not job.artifact_dir:
        return None
    path = os.path.join(job.artifact_dir, *subpath)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─── Health & Credentials ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/credentials")
async def credentials():
    return {"credentials": credential_report()}


# ─── Manual CRUD ──────────────────────────────────────────────────────────

@app.post("/v1/manuals")
async def create_manual(
    images: Annotated[list[UploadFile], File(description="One or more object images")],
    goal: Annotated[str | None, Form()] = None,
    mode: Annotated[str, Form()] = "simple",
):
    """Accept image uploads and start the visual manual pipeline."""
    settings = load_settings()

    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required")

    # File type / size validation
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    max_size = 20 * 1024 * 1024  # 20MB
    for photo in images:
        if photo.content_type and photo.content_type not in allowed_types:
            raise HTTPException(status_code=415, detail=f"Unsupported file type: {photo.content_type}")
        content = await photo.read()
        if len(content) > max_size:
            raise HTTPException(status_code=413, detail="File too large (max 20MB)")
        await photo.seek(0)

    os.makedirs(settings.upload_dir, exist_ok=True)

    # Save uploaded images
    input_images = []
    for i, photo in enumerate(images):
        content = await photo.read()
        upload_path = os.path.join(settings.upload_dir, f"{int(time.time())}_{i}_{photo.filename}")
        with open(upload_path, "wb") as f:
            f.write(content)

        img_entry = {
            "url": f"file://{upload_path}",
            "public_url": f"file://{upload_path}",
            "role": "main object photo" if i == 0 else f"additional view {i}",
        }

        # Try to upload to GMI Inference Storage
        if settings.gmi_api_key:
            from .adapters.gmi_ie import upload_file
            file_ext = os.path.splitext(photo.filename)[1].lstrip(".")
            if not file_ext:
                file_ext = "png"
            try:
                public_url = await upload_file(upload_path, file_type=file_ext)
                if public_url:
                    img_entry["public_url"] = public_url
            except Exception:
                pass  # Keep file:// fallback

        input_images.append(img_entry)

    # Create job
    job = create_job()

    # Start pipeline in background
    asyncio.create_task(_run_job_pipeline(job.job_id, input_images, goal))

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "job_id": job.job_id,
            "status": "queued",
            "simple_message": "Images received. Building the visual manual.",
        },
    )


async def _run_job_pipeline(job_id: str, input_images: list[dict], goal: str | None):
    """Run the pipeline and update job state (delegates to shared runner)."""
    await run_and_persist(job_id, input_images, goal)


async def _save_upload(content: bytes, filename: str, job_id: str, index: int = 0) -> dict:
    """Persist an uploaded image and return an input_images entry.

    On Vercel (blob enabled) the image goes to Blob and we use its public URL.
    Locally we write to upload_dir and serve via /uploads.
    """
    settings = load_settings()
    safe_name = os.path.basename(filename or f"image_{index}.png")
    if storage.is_blob():
        key = f"uploads/{job_id}/{safe_name}"
        ext = os.path.splitext(safe_name)[1].lstrip(".").lower() or "png"
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
        url = await storage.put_bytes(key, content, ctype)
        return {"url": url, "public_url": url, "role": "main object photo" if index == 0 else f"additional view {index}"}

    os.makedirs(settings.upload_dir, exist_ok=True)
    upload_path = os.path.join(settings.upload_dir, f"{int(time.time())}_{index}_{safe_name}")
    with open(upload_path, "wb") as f:
        f.write(content)
    return {"url": f"file://{upload_path}", "public_url": f"file://{upload_path}", "role": "main object photo" if index == 0 else f"additional view {index}"}


async def _run_pipeline_sync(job_id: str, input_images: list[dict], goal: str | None) -> None:
    """Run the full pipeline synchronously and persist the result.

    Serverless functions terminate after responding, so we cannot fire-and-forget.
    """
    await _run_job_pipeline(job_id, input_images, goal)


async def _render_pdf_to_tmp(job) -> str | None:
    """Render the manual PDF on-demand from manual_json into /tmp. Returns path or None."""
    if not job.manual_json:
        return None
    # Resolve a local source image path (download from Blob/http if needed).
    source_image_path = ""
    if job.input_images:
        raw = job.input_images[0].get("url", "") or job.input_images[0].get("public_url", "")
        if raw.startswith("file://"):
            local = raw.replace("file://", "")
            if os.path.exists(local):
                source_image_path = local
        elif raw.startswith("http"):
            try:
                data = await storage.get_bytes_from_url(raw)
                if data:
                    ext = os.path.splitext(raw.split("?")[0])[1] or ".png"
                    tmp_img = os.path.join(tempfile.gettempdir(), f"{job.job_id}_src{ext}")
                    with open(tmp_img, "wb") as f:
                        f.write(data)
                    source_image_path = tmp_img
            except Exception:
                pass
    pdf_path = os.path.join(tempfile.gettempdir(), f"{job.job_id}_manual.pdf")
    ok = render_pdf(job.manual_json, pdf_path, source_image_path)
    return pdf_path if ok and os.path.exists(pdf_path) else None


@app.get("/api/jobs/{job_id}/manual.pdf")
async def parallax_get_manual_pdf(job_id: str):
    """Serve the manual PDF, regenerated on-demand from manual_json (serverless-safe)."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    pdf_path = await _render_pdf_to_tmp(job)
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF not available")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{job_id}-manual.pdf")


@app.get("/v1/manuals/{job_id}")
async def get_manual_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.get("/v1/manuals/{job_id}/events")
async def stream_events(job_id: str):
    """SSE stream for job progress updates with spec-compliant event names."""
    async def event_stream():
        last_progress = -1
        last_status = ""
        while True:
            job = get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            if job.progress != last_progress or job.status != last_status:
                last_progress = job.progress
                last_status = job.status
                event_name = _status_to_event(job.status)
                event_data = {
                    "event": event_name,
                    "job_id": job.job_id,
                    "status": job.status,
                    "progress": job.progress,
                    "simple_message": job.simple_message,
                }
                yield f"event: {event_name}\ndata: {json.dumps(event_data)}\n\n"

            if job.status in ("completed", "partial", "blocked"):
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ─── Artifacts ────────────────────────────────────────────────────────────

@app.get("/v1/manuals/{job_id}/artifacts")
async def get_artifacts(job_id: str):
    """Aggregated artifact URLs and frame lists."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")

    explode_frames = job.explode.get("frames", [])
    turntable_frames = job.turntable.get("frames", [])

    return {
        "job_id": job_id,
        "manual_json_url": f"/v1/manuals/{job_id}/artifacts/manual.json",
        "html_url": f"/v1/manuals/{job_id}/artifacts/index.html",
        "pdf_url": f"/v1/manuals/{job_id}/artifacts/manual.pdf",
        "parts_json_url": f"/v1/manuals/{job_id}/artifacts/parts.json",
        "exploded_frames": explode_frames,
        "turntable_frames": turntable_frames,
        "status": job.status,
    }


@app.get("/v1/manuals/{job_id}/artifacts/manual.pdf")
async def get_manual_pdf(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    pdf_path = os.path.join(job.artifact_dir, "guide", "manual.pdf")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not rendered")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{job_id}-manual.pdf")


@app.get("/v1/manuals/{job_id}/artifacts/index.html")
async def get_manual_html(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    html_path = os.path.join(job.artifact_dir, "guide", "index.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="HTML not rendered")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/v1/manuals/{job_id}/artifacts/manual.json")
async def get_manual_json(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    json_path = os.path.join(job.artifact_dir, "guide", "manual.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="manual.json not found")
    with open(json_path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


@app.get("/v1/manuals/{job_id}/artifacts/parts.json")
async def get_parts_json(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    parts_path = os.path.join(job.artifact_dir, "guide", "parts.json")
    if not os.path.exists(parts_path):
        raise HTTPException(status_code=404, detail="parts.json not found")
    with open(parts_path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


@app.get("/v1/manuals/{job_id}/artifacts/citations.json")
async def get_citations_json(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    cit_path = os.path.join(job.artifact_dir, "guide", "citations.json")
    if not os.path.exists(cit_path):
        raise HTTPException(status_code=404, detail="citations.json not found")
    with open(cit_path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


@app.get("/v1/manuals/{job_id}/artifacts/overlay.json")
async def get_overlay_json(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    ov_path = os.path.join(job.artifact_dir, "guide", "overlay.json")
    if not os.path.exists(ov_path):
        raise HTTPException(status_code=404, detail="overlay.json not found")
    with open(ov_path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


# ─── Part Cards ───────────────────────────────────────────────────────────

@app.get("/v1/manuals/{job_id}/parts/{part_id}")
async def get_part_card(job_id: str, part_id: str):
    """Return a single part card from the manual artifact."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Artifacts not ready")
    parts_path = os.path.join(job.artifact_dir, "guide", "parts.json")
    if not os.path.exists(parts_path):
        raise HTTPException(status_code=404, detail="Parts not found")
    with open(parts_path, "r", encoding="utf-8") as f:
        parts = json.load(f)
    part = next((p for p in parts if p.get("id") == part_id), None)
    if not part:
        raise HTTPException(status_code=404, detail=f"Part '{part_id}' not found")
    return part


# ─── Ask ──────────────────────────────────────────────────────────────────

@app.post("/v1/manuals/{job_id}/ask")
async def ask_manual(
    job_id: str,
    question: Annotated[str, Form()],
):
    """Ask a follow-up question against the manual artifact."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.manual_json:
        raise HTTPException(status_code=400, detail="Manual not ready")

    manual = job.manual_json
    parts = manual.get("parts", [])
    obj = manual.get("object", {})
    warnings = manual.get("warnings", [])
    non_claims = manual.get("non_claims", [])
    steps = manual.get("steps", [])

    context = f"""Object: {obj.get('type', 'unknown')} (model: {obj.get('likely_model', 'unknown')})
Summary: {obj.get('summary', '')}
Parts: {json.dumps([{'id': p.get('id'), 'label': p.get('label'), 'function': p.get('function', ''), 'description': p.get('description', ''), 'confidence': p.get('confidence')} for p in parts], indent=2)}
Steps: {json.dumps([{'title': s.get('title'), 'instruction': s.get('instruction')} for s in steps], indent=2)}
Warnings: {json.dumps(warnings)}
Limitations: {json.dumps(non_claims)}

Question: {question}

Answer based ONLY on the manual data above. If the answer is not in the manual, say 'This information is not available in the visual manual.' Do not make up information."""

    answer = await gmi_chat(context, system="You are a visual manual assistant. Answer questions based only on the provided manual data.")

    # Find relevant parts
    relevant_part_ids = []
    question_lower = question.lower()
    for p in parts:
        if p.get("label", "").lower() in question_lower or p.get("id", "").lower() in question_lower:
            relevant_part_ids.append(p["id"])

    return {
        "job_id": job_id,
        "question": question,
        "answer": answer or "Unable to generate answer (LLM not configured).",
        "part_ids": relevant_part_ids,
        "citations": [],
        "warnings": ["Exact model-specific details require official documentation."] if not relevant_part_ids else [],
        "manual_context": {
            "object_type": obj.get("type", ""),
            "parts_count": len(parts),
        },
    }


# ─── Export ───────────────────────────────────────────────────────────────

@app.post("/v1/manuals/{job_id}/export")
async def export_manual(job_id: str):
    """Return artifact download URLs for a completed job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.artifact_dir:
        raise HTTPException(status_code=400, detail="No artifact to export")

    return {
        "job_id": job_id,
        "pdf_url": f"/v1/manuals/{job_id}/artifacts/manual.pdf",
        "html_url": f"/v1/manuals/{job_id}/artifacts/index.html",
        "json_url": f"/v1/manuals/{job_id}/artifacts/manual.json",
    }


# ─── Validate ─────────────────────────────────────────────────────────────

@app.post("/v1/manuals/{job_id}/validate")
async def validate_manual_claims(job_id: str):
    """Validate manual claims — check for unsupported claims, missing citations, unsafe content."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.manual_json:
        raise HTTPException(status_code=400, detail="Manual not ready")

    manual = job.manual_json
    parts = manual.get("parts", [])
    citations = manual.get("citations", [])
    warnings = manual.get("warnings", [])
    non_claims = manual.get("non_claims", [])

    unsupported_claims = []
    unsafe_claims = []
    missing_citations = []

    # Check each part for unsupported claims
    for p in parts:
        conf = p.get("confidence", 0)
        source_status = p.get("source_status", "vision_inferred")

        # High confidence without search support = potentially unsupported
        if conf > 0.8 and source_status == "vision_inferred":
            unsupported_claims.append({
                "part_id": p.get("id"),
                "claim": p.get("label", ""),
                "reason": "High confidence without source verification",
            })

        # Check for safety-related terms without citations
        desc = p.get("description", "").lower()
        safety_terms = ["safe", "danger", "voltage", "electrical", "repair", "install", "wiring"]
        for term in safety_terms:
            if term in desc and not p.get("sources"):
                unsafe_claims.append({
                    "part_id": p.get("id"),
                    "claim": p.get("description", ""),
                    "reason": f"Safety-related term '{term}' used without source backing",
                })
                break

    # Check for missing citations on search-supported parts
    for p in parts:
        if p.get("source_status") == "search_supported" and not p.get("sources"):
            missing_citations.append({
                "part_id": p.get("id"),
                "reason": "source_status is search_supported but no sources listed",
            })

    # Check standard warnings exist
    required_warnings = ["AI-generated", "not manufacturer-certified"]
    has_warnings = " ".join(warnings).lower()
    missing_warnings = [w for w in required_warnings if w.lower() not in has_warnings]

    # Verdict
    if unsafe_claims:
        verdict = "FAIL"
    elif unsupported_claims or missing_citations:
        verdict = "WARNING"
    else:
        verdict = "PASS"

    return {
        "job_id": job_id,
        "verdict": verdict,
        "unsupported_claims": unsupported_claims,
        "missing_citations": missing_citations,
        "unsafe_claims": unsafe_claims,
        "missing_warnings": missing_warnings,
        "parts_checked": len(parts),
        "citations_count": len(citations),
    }


# ─── MCP Layer ────────────────────────────────────────────────────────────

_MCP_RESOURCES = [
    {"uri": "manual://jobs/{job_id}/manual.json", "name": "Manual JSON", "description": "The canonical manual artifact"},
    {"uri": "manual://jobs/{job_id}/manual.pdf", "name": "Manual PDF", "description": "Rendered PDF manual"},
    {"uri": "manual://jobs/{job_id}/index.html", "name": "Manual HTML", "description": "Interactive HTML manual"},
    {"uri": "manual://jobs/{job_id}/parts", "name": "Parts List", "description": "All parts as JSON"},
    {"uri": "manual://jobs/{job_id}/parts/{part_id}", "name": "Part Card", "description": "Single part detail"},
    {"uri": "manual://jobs/{job_id}/citations", "name": "Citations", "description": "All citations"},
    {"uri": "manual://jobs/{job_id}/frames/exploded", "name": "Exploded Frames", "description": "Exploded view frame URLs"},
    {"uri": "manual://jobs/{job_id}/frames/turntable", "name": "Turntable Frames", "description": "360 preview frame URLs"},
    {"uri": "manual://jobs/{job_id}/proof", "name": "Proof Receipt", "description": "Artifact proof receipt"},
]

_MCP_TOOLS = [
    {"name": "create_visual_manual", "description": "Create a visual manual from image URLs"},
    {"name": "get_manual_status", "description": "Get job status and progress"},
    {"name": "search_manual_artifact", "description": "Search parts and sections by query"},
    {"name": "get_part_card", "description": "Get a single part card by ID"},
    {"name": "render_pdf_manual", "description": "Get PDF download URL"},
    {"name": "render_html_manual", "description": "Get HTML URL"},
    {"name": "generate_exploded_view", "description": "Get exploded view frames"},
    {"name": "generate_360_preview", "description": "Get 360 preview frames"},
    {"name": "validate_manual_claims", "description": "Validate claims for safety and citation backing"},
]

_MCP_PROMPTS = [
    {"name": "visual_manual_from_images", "description": "Generate a visual manual from object images"},
    {"name": "part_breakdown_from_image", "description": "Break down an object into parts from an image"},
    {"name": "safe_visual_manual_answer", "description": "Answer questions safely using manual data only"},
    {"name": "explode_view_prompt_builder", "description": "Build a Kling explode-view prompt"},
    {"name": "manual_pdf_renderer_prompt", "description": "Plan PDF rendering from manual.json"},
]


@app.get("/mcp/resources")
async def mcp_list_resources():
    return {"resources": _MCP_RESOURCES}


@app.get("/mcp/resources/{uri:path}")
async def mcp_read_resource(uri: str):
    """Read a manual resource by URI like manual://jobs/{job_id}/manual.json"""
    # Parse: manual://jobs/{job_id}/{resource}
    parts = uri.replace("manual://", "").split("/")
    if len(parts) < 3 or parts[0] != "jobs":
        raise HTTPException(status_code=400, detail="Invalid resource URI")
    job_id = parts[1]
    resource = "/".join(parts[2:])

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if resource == "manual.json":
        content = _read_artifact_file(job_id, "guide", "manual.json")
        if content:
            return json.loads(content)
        raise HTTPException(status_code=404, detail="manual.json not found")
    elif resource == "index.html":
        content = _read_artifact_file(job_id, "guide", "index.html")
        if content:
            return HTMLResponse(content=content)
        raise HTTPException(status_code=404, detail="index.html not found")
    elif resource == "manual.pdf":
        job_obj = get_job(job_id)
        if job_obj and job_obj.artifact_dir:
            pdf_path = os.path.join(job_obj.artifact_dir, "guide", "manual.pdf")
            if os.path.exists(pdf_path):
                return FileResponse(pdf_path, media_type="application/pdf")
        raise HTTPException(status_code=404, detail="PDF not found")
    elif resource == "parts":
        content = _read_artifact_file(job_id, "guide", "parts.json")
        if content:
            return json.loads(content)
        raise HTTPException(status_code=404, detail="parts.json not found")
    elif resource.startswith("parts/"):
        part_id = resource.split("/")[1]
        content = _read_artifact_file(job_id, "guide", "parts.json")
        if content:
            all_parts = json.loads(content)
            part = next((p for p in all_parts if p.get("id") == part_id), None)
            if part:
                return part
            raise HTTPException(status_code=404, detail=f"Part '{part_id}' not found")
        raise HTTPException(status_code=404, detail="parts.json not found")
    elif resource == "citations":
        content = _read_artifact_file(job_id, "guide", "citations.json")
        if content:
            return json.loads(content)
        raise HTTPException(status_code=404, detail="citations.json not found")
    elif resource == "frames/exploded":
        return {"frames": job.explode.get("frames", []), "status": job.explode.get("status", "blocked")}
    elif resource == "frames/turntable":
        return {"frames": job.turntable.get("frames", []), "status": job.turntable.get("status", "blocked")}
    elif resource == "proof":
        content = _read_artifact_file(job_id, "proof", "receipt.json")
        if content:
            return json.loads(content)
        raise HTTPException(status_code=404, detail="receipt.json not found")
    else:
        raise HTTPException(status_code=404, detail=f"Unknown resource: {resource}")


@app.get("/mcp/tools")
async def mcp_list_tools():
    return {"tools": _MCP_TOOLS}


@app.post("/mcp/tools/{tool_name}")
async def mcp_call_tool(tool_name: str, request: Request):
    """Call an MCP tool."""
    body = await request.json()
    args = body.get("args", body)

    if tool_name == "create_visual_manual":
        return {
            "tool": tool_name,
            "job_id": "Use POST /v1/manuals with multipart upload",
            "note": "This tool requires image upload. Use the REST endpoint directly.",
        }
    elif tool_name == "get_manual_status":
        job_id = args.get("job_id", "")
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "status": job.status,
            "progress": job.progress,
            "simple_message": job.simple_message,
            "artifact_urls": {
                "manual_json": f"/v1/manuals/{job_id}/artifacts/manual.json",
                "html": f"/v1/manuals/{job_id}/artifacts/index.html",
                "pdf": f"/v1/manuals/{job_id}/artifacts/manual.pdf",
            } if job.artifact_dir else None,
        }
    elif tool_name == "search_manual_artifact":
        job_id = args.get("job_id", "")
        query = args.get("query", "").lower()
        job = get_job(job_id)
        if not job or not job.manual_json:
            raise HTTPException(status_code=404, detail="Manual not found")
        manual = job.manual_json
        matching_parts = [p for p in manual.get("parts", []) if query in p.get("label", "").lower() or query in p.get("description", "").lower()]
        matching_sections = [s for s in manual.get("sections", []) if query in s.get("title", "").lower()]
        return {
            "matching_parts": matching_parts,
            "matching_sections": matching_sections,
            "citations": [c for c in manual.get("citations", []) if query in c.get("used_for", "").lower()],
        }
    elif tool_name == "get_part_card":
        job_id = args.get("job_id", "")
        part_id = args.get("part_id", "")
        content = _read_artifact_file(job_id, "guide", "parts.json")
        if content:
            all_parts = json.loads(content)
            part = next((p for p in all_parts if p.get("id") == part_id), None)
            if part:
                return part
        raise HTTPException(status_code=404, detail="Part not found")
    elif tool_name == "render_pdf_manual":
        job_id = args.get("job_id", "")
        return {"pdf_url": f"/v1/manuals/{job_id}/artifacts/manual.pdf"}
    elif tool_name == "render_html_manual":
        job_id = args.get("job_id", "")
        return {"html_url": f"/v1/manuals/{job_id}/artifacts/index.html"}
    elif tool_name == "generate_exploded_view":
        job_id = args.get("job_id", "")
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"video_url": job.explode.get("video_url", ""), "frame_urls": job.explode.get("frames", [])}
    elif tool_name == "generate_360_preview":
        job_id = args.get("job_id", "")
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"video_url": job.turntable.get("video_url", ""), "frame_urls": job.turntable.get("frames", [])}
    elif tool_name == "validate_manual_claims":
        job_id = args.get("job_id", "")
        # Reuse the validate endpoint logic
        job = get_job(job_id)
        if not job or not job.manual_json:
            raise HTTPException(status_code=404, detail="Manual not found")
        manual = job.manual_json
        parts = manual.get("parts", [])
        unsupported = [p["id"] for p in parts if p.get("confidence", 0) > 0.8 and p.get("source_status") == "vision_inferred"]
        unsafe = [p["id"] for p in parts if any(t in p.get("description", "").lower() for t in ["safe", "danger", "voltage", "repair"]) and not p.get("sources")]
        return {
            "unsupported_claims": unsupported,
            "missing_citations": [p["id"] for p in parts if p.get("source_status") == "search_supported" and not p.get("sources")],
            "unsafe_claims": unsafe,
            "verdict": "FAIL" if unsafe else ("WARNING" if unsupported else "PASS"),
        }
    else:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")


@app.get("/mcp/prompts")
async def mcp_list_prompts():
    return {"prompts": _MCP_PROMPTS}


# ─── Stub Frontend ────────────────────────────────────────────────────────

_stub_html = None


@app.get("/stub", response_class=HTMLResponse)
async def stub():
    global _stub_html
    if _stub_html is None:
        stub_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stub", "index.html")
        if os.path.exists(stub_path):
            with open(stub_path, "r", encoding="utf-8") as f:
                _stub_html = f.read()
        else:
            _stub_html = "<html><body><h1>Stub not found</h1></body></html>"
    return _stub_html


@app.get("/", response_class=HTMLResponse)
async def root():
    return await stub()


# ─── Parallax Frontend Adapter Routes ─────────────────────────────────────
# These routes adapt our manual pipeline to the CONTRACT.md API expected by
# the kevincui1034/parallax frontend:
#   POST /api/generate  (multipart: image) → {job_id, status, progress, result, error}
#   GET  /api/jobs/{job_id}               → same shape
#   POST /api/agent                        → {reply, actions}

@app.post("/api/generate", status_code=202)
async def parallax_generate(image: Annotated[UploadFile, File(description="Product photo")]):
    """Accept a single image, start the manual pipeline, return parallax-compatible job."""
    settings = load_settings()

    if not image:
        raise HTTPException(status_code=400, detail="Image is required")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if image.content_type and image.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {image.content_type}")
    content = await image.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")
    await image.seek(0)

    job = create_job()
    input_images = [await _save_upload(content, image.filename or "image.png", job.job_id, 0)]
    update_job(job.job_id, input_images=input_images)

    if storage.is_blob():
        # Serverless: run synchronously so the work completes before the function exits.
        await _run_pipeline_sync(job.job_id, input_images, None)
        final = get_job(job.job_id)
        return {
            "job_id": job.job_id,
            "status": "running" if (final and final.status not in ("completed", "partial", "blocked")) else "done",
            "progress": final.progress if final else 0,
            "result": None,
            "error": None,
        }

    asyncio.create_task(_run_job_pipeline(job.job_id, input_images, None))
    return {
        "job_id": job.job_id,
        "status": "queued",
        "progress": 0,
        "result": None,
        "error": None,
    }


@app.get("/api/jobs/{job_id}")
async def parallax_get_job(job_id: str):
    """Return job state in parallax CONTRACT.md format."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    # Map our status to parallax status
    status_map = {
        "queued": "queued",
        "understanding": "running",
        "searching": "running",
        "planning": "running",
        "generating": "running",
        "generating_exploded_video": "running",
        "extracting": "running",
        "rendering": "running",
        "rendering_pdf": "running",
        "validating": "running",
        "completed": "done",
        "partial": "done",
        "blocked": "error",
    }
    px_status = status_map.get(job.status, "running")

    # Build ModelResult from our parts data
    result = None
    error = None
    if px_status == "done":
        parts = job.parts or []
        px_parts = []
        for i, p in enumerate(parts):
            px_parts.append({
                "part_id": p.get("id", f"p{i}"),
                "label": p.get("label", f"part_{i}"),
                "description": p.get("description", ""),
                "confidence": p.get("confidence", 0.0),
                "source_status": p.get("source_status", "vision_inferred"),
                "sources": p.get("sources", []),
                "visual_evidence": p.get("visual_evidence", ""),
                "unknowns": p.get("unknowns", []),
                "model_url": p.get("model_url", ""),
                "centroid": p.get("centroid", [0.0, 0.0, 0.0]),
                "bbox": p.get("bbox", {"min": [-1, -1, -1], "max": [1, 1, 1]}),
            })

        # Resolve source image URL — convert file:// path to /uploads path
        source_image_url = ""
        if job.input_images:
            raw = job.input_images[0].get("public_url", "") or job.input_images[0].get("url", "")
            if raw.startswith("file://"):
                fname = os.path.basename(raw.replace("file://", ""))
                source_image_url = f"/uploads/{fname}"
            elif raw.startswith("http"):
                source_image_url = raw

        # Manual HTML + PDF URLs (regenerated on-demand from manual_json)
        has_manual = bool(job.manual_json or job.artifact_dir)
        manual_url = f"/api/jobs/{job_id}/manual" if has_manual else ""
        pdf_url = f"/api/jobs/{job_id}/manual.pdf" if has_manual else ""

        # Frames for 2D scrubbing
        explode_frames = job.explode.get("frames", []) if job.explode else []
        turntable_frames = job.turntable.get("frames", []) if job.turntable else []

        result = {
            "model_id": job.job_id,
            "source_image_url": source_image_url,
            "manual_url": manual_url,
            "pdf_url": pdf_url,
            "center": [0.0, 0.0, 0.0],
            "bbox": {"min": [-1, -1, -1], "max": [1, 1, 1]},
            "parts": px_parts,
            "object_type": job.object_type or "",
            "likely_model": job.likely_model or "",
            "object_summary": job.object_summary or "",
            "object_confidence": job.object_confidence or 0.0,
            "citations": job.citations or [],
            "steps": job.steps or [],
            "warnings": job.warnings or [],
            "non_claims": job.non_claims or [],
            "explode_frames": explode_frames,
            "turntable_frames": turntable_frames,
            "snaplii_actions": job.snaplii_actions or [],
        }
    elif px_status == "error":
        error = job.simple_message or "Pipeline failed"

    return {
        "job_id": job.job_id,
        "status": px_status,
        "progress": job.progress,
        "result": result,
        "error": error,
    }


@app.get("/api/jobs/{job_id}/manual")
async def parallax_get_manual(job_id: str):
    """Serve the HTML manual for iframe embedding in the frontend.

    Regenerated from the embedded manual_json so it works on serverless (no
    dependency on a persisted artifact directory).
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.manual_json:
        return HTMLResponse(content=_build_manual_html(job.manual_json))
    if job.artifact_dir:
        html_path = os.path.join(job.artifact_dir, "guide", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Manual not ready")


@app.post("/api/agent")
async def parallax_agent(req: Request):
    """Agent endpoint matching CONTRACT.md — uses Gemini to reason about parts.

    Request body: {model_id, message, explode_factor}
    Response: {reply, actions: [{type, ...}]}
    """
    body = await req.json()
    model_id = body.get("model_id", "")
    message = body.get("message", "")
    explode_factor = body.get("explode_factor", 0)

    job = get_job(model_id)
    if not job:
        return {
            "reply": "Model not found. Please upload an image first.",
            "actions": [{"type": "reset"}],
        }

    # Build part inventory for the prompt
    parts = job.parts or []
    part_list_str = "\n".join(
        f"  - {p.get('id', f'p{i}')}: {p.get('label', 'unknown')} — {p.get('description', '')}"
        for i, p in enumerate(parts)
    )
    object_name = job.likely_model or job.object_type or "the object"

    system_prompt = f"""You are an expert technical assistant for {object_name}.
You can see the following parts in an exploded-parts diagram:
{part_list_str}

The current explode factor is {explode_factor} (0=assembled, 1=fully exploded).

Respond with a JSON object containing:
  "reply": a helpful explanation for the user
  "actions": an array of actions to execute on the viewer

Valid action types:
  {{"type": "explode", "factor": <0-1>}}
  {{"type": "highlight", "part_id": "<id>"}}
  {{"type": "isolate", "part_ids": ["<id>", ...]}}
  {{"type": "focus", "part_id": "<id>"}}
  {{"type": "reset"}}

Only use part IDs from the list above. Return ONLY valid JSON, no markdown."""

    try:
        response_text = await gmi_chat(message, system=system_prompt)
        parsed = None
        if response_text:
            from .llm import parse_json_response
            parsed = parse_json_response(response_text)

        if parsed and "reply" in parsed:
            # Validate actions
            valid_types = {"explode", "highlight", "isolate", "focus", "reset"}
            actions = []
            for a in parsed.get("actions", []):
                if isinstance(a, dict) and a.get("type") in valid_types:
                    actions.append(a)
            return {"reply": parsed["reply"], "actions": actions, "citations": job.citations or []}
        else:
            return {
                "reply": response_text or f"I couldn't process that request about {object_name}.",
                "actions": [],
                "citations": job.citations or [],
            }
    except Exception as e:
        logger.error("parallax agent error: %s", e)
        return {
            "reply": f"Sorry, I encountered an error: {e}",
            "actions": [],
            "citations": [],
        }


# ─── Snaplii Post-Manual Action Layer ──────────────────────────────────────
# Snaplii acts as a completion layer AFTER the manual is generated.
# All actions require user approval — no auto-purchase, no auto-send.

@app.post("/v1/manuals/{job_id}/snaplii/actions")
async def create_snaplii_action(job_id: str, request: Request):
    """Create a Snaplii action card for a completed manual.

    Body: {"action_type": "manual_card" | "parts_action" | "reward_claim", "label": str (optional)}
    Returns the action card dict. Returns mock card if Snaplii API is not configured.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail="Manual not ready — Snaplii actions available after completion")

    body = await request.json()
    action_type = body.get("action_type", "manual_card")
    label = body.get("label", "")

    base = str(request.base_url).rstrip("/")
    manual_url = f"{base}/api/jobs/{job_id}/manual"

    if action_type == "manual_card":
        action = await snaplii_adapter.create_manual_card(
            job_id, manual_url=manual_url, label=label or "Save / Share with Snaplii"
        )
    elif action_type == "parts_action":
        action = await snaplii_adapter.create_parts_action(
            job_id, parts=job.parts or [], label=label or "View Parts & Tools on Snaplii"
        )
    elif action_type == "reward_claim":
        action = await snaplii_adapter.create_reward_claim(
            job_id, label=label or "Claim Your Reward"
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action_type: {action_type}")

    # Persist the action on the job
    current_actions = job.snaplii_actions or []
    current_actions.append(action)
    update_job(job_id, snaplii_actions=current_actions)

    return action


@app.get("/v1/manuals/{job_id}/snaplii/actions/{action_id}")
async def get_snaplii_action(job_id: str, action_id: str):
    """Get the status of a specific Snaplii action."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check persisted actions first
    for a in (job.snaplii_actions or []):
        if a.get("id") == action_id:
            return a

    # Fall back to adapter status check
    status = await snaplii_adapter.get_action_status(action_id)
    if status:
        return status

    raise HTTPException(status_code=404, detail="Snaplii action not found")


@app.get("/v1/manuals/{job_id}/snaplii/actions")
async def list_snaplii_actions(job_id: str):
    """List all Snaplii actions for a job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "actions": job.snaplii_actions or []}


@app.post("/v1/webhooks/snaplii")
async def snaplii_webhook(request: Request):
    """Webhook endpoint for Snaplii status callbacks."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    result = await snaplii_adapter.handle_webhook(payload)

    # Update persisted action status if action_id is present
    action_id = payload.get("action_id", "")
    new_status = payload.get("status", "")
    if action_id and new_status:
        for jid, job in list(_store.items()):
            actions = job.snaplii_actions or []
            for a in actions:
                if a.get("id") == action_id:
                    a["status"] = new_status
                    update_job(jid, snaplii_actions=actions)
                    break

    return result
