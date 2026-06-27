"""
Parallax backend — FastAPI, all models routed through GMI Cloud.

- /api/agent      → 3.5 Flash (validated action protocol)            [gmi.py, agent.py]
- /api/generate   → 2D path: GPT-Image-2-Edit → Kling V3 exploded video
                    3D path: stub (PartCrafter unavailable on GMI)
Falls back to safe stubs when GMI_API_KEY is unset so the frontend never blocks.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import agent as agent_mod
import gmi
from contract import (
    AgentRequest,
    AgentResponse,
    BBox,
    Job,
    ModelResult,
    Part,
    ResetAction,
    TwoDResult,
)

load_dotenv()

FILES_DIR = Path(__file__).parent / "files"
FILES_DIR.mkdir(exist_ok=True)

# 2D pipeline config (tune the prompts without touching code).
IMAGE_PROMPT = os.getenv(
    "GMI_2D_IMAGE_PROMPT",
    "Clean product render of this object, centered, full object visible, "
    "neutral dark studio background, high detail, no text.",
)
VIDEO_PROMPT = os.getenv(
    "GMI_2D_VIDEO_PROMPT",
    "The object smoothly disassembles into a technical exploded-parts diagram: "
    "each component separates and floats apart along the assembly axis, evenly "
    "spaced, dark studio background, slow controlled motion, no camera shake.",
)
VIDEO_DURATION = int(os.getenv("GMI_2D_DURATION", "5"))
NUM_ANGLES = int(os.getenv("GMI_2D_ANGLES", "1"))
ASSUMED_FPS = 16

# Demo part inventory the agent can reference until a real ModelResult is
# persisted per model_id (matches the frontend's engine assembly).
DEMO_INVENTORY = [
    {"part_id": "P-01", "label": "Cylinder Sleeve"},
    {"part_id": "P-02", "label": "Piston"},
    {"part_id": "P-03", "label": "Compression Ring"},
    {"part_id": "P-04", "label": "Wrist Pin"},
    {"part_id": "P-05", "label": "Connecting Rod"},
    {"part_id": "P-06", "label": "Crank Journal"},
    {"part_id": "P-07", "label": "Intake Valve"},
    {"part_id": "P-08", "label": "Valve Spring"},
]

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(title="Parallax Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # preview deploys
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=str(FILES_DIR)), name="files")

JOBS: dict[str, Job] = {}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "gmi_configured": str(gmi.is_configured()).lower()}


@app.post("/api/generate", status_code=202)
async def generate(
    image: UploadFile = File(...),
    mode: str = Form("2d"),  # "2d" (image→video) | "3d" (parts, stubbed)
) -> Job:
    """Accept an image, queue a generation job, return immediately."""
    job_id = str(uuid.uuid4())
    model_id = str(uuid.uuid4())

    model_dir = FILES_DIR / model_id
    model_dir.mkdir(parents=True, exist_ok=True)
    input_path = model_dir / "input.png"
    with input_path.open("wb") as f:
        shutil.copyfileobj(image.file, f)

    job = Job(job_id=job_id, status="queued", progress=0)
    JOBS[job_id] = job
    if mode == "3d":
        asyncio.create_task(_run_generation_3d(job_id, model_id))
    else:
        asyncio.create_task(_run_generation_2d(job_id, model_id, input_path))
    return job


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/agent")
async def agent(req: AgentRequest) -> AgentResponse:
    """3.5 Flash → validated {reply, actions}. Falls back to a stub if GMI is
    unconfigured or errors, so the demo loop never breaks."""
    if not gmi.is_configured():
        return AgentResponse(
            reply=f"(stub — set GMI_API_KEY) You asked: {req.message!r}",
            actions=[ResetAction(type="reset")],
        )
    try:
        return await agent_mod.run_agent(req, parts=DEMO_INVENTORY)
    except Exception as exc:
        return AgentResponse(reply=f"Agent error: {exc}", actions=[])


async def _run_generation_2d(job_id: str, model_id: str, input_path: Path) -> None:
    """2D pipeline: GPT-Image-2-Edit (part + multi-angle) → Kling V3 video."""
    job = JOBS[job_id]
    try:
        job.status = "running"
        if not gmi.is_configured():
            raise RuntimeError("GMI_API_KEY not set")

        job.progress = 10
        img_b64 = base64.b64encode(input_path.read_bytes()).decode()
        data_uri = f"data:image/png;base64,{img_b64}"

        # 1) generate the part / multi-angle shots from the uploaded photo
        angles = await gmi.generate_part_images(
            IMAGE_PROMPT,
            image_b64=data_uri,
            n=NUM_ANGLES,
            on_status=lambda s: _bump(job, 15, 40),
        )
        if not angles:
            raise RuntimeError("image model returned no images")
        job.progress = 45

        # 2) turn the hero shot into an exploded-view clip
        video_url = await gmi.image_to_video(
            angles[0],
            VIDEO_PROMPT,
            duration=VIDEO_DURATION,
            on_status=lambda s: _bump(job, 50, 95),
        )

        job.result = TwoDResult(
            model_id=model_id,
            source_image_url=f"/files/{model_id}/input.png",
            video_url=video_url,
            frame_count=VIDEO_DURATION * ASSUMED_FPS,
            angles=angles,
        )
        job.progress = 100
        job.status = "done"
    except Exception as exc:  # never let a job hang
        job.status = "error"
        job.error = str(exc)


def _bump(job: Job, lo: int, hi: int) -> None:
    """Nudge progress upward within a phase's [lo, hi] band on each poll tick."""
    job.progress = min(hi, max(lo, job.progress + 5))


async def _run_generation_3d(job_id: str, model_id: str) -> None:
    """3D path stub — PartCrafter is unavailable on GMI. Returns a one-part
    ModelResult so the 3D viewer has the right shape."""
    job = JOBS[job_id]
    try:
        job.status = "running"
        for pct in (20, 50, 80):
            await asyncio.sleep(1.0)
            job.progress = pct
        job.result = ModelResult(
            model_id=model_id,
            source_image_url=f"/files/{model_id}/input.png",
            center=(0.0, 0.0, 0.0),
            bbox=BBox(min=(-1, -1, -1), max=(1, 1, 1)),
            parts=[
                Part(
                    part_id="p0",
                    label="part_0",
                    model_url=f"/files/{model_id}/p0.glb",
                    centroid=(0.0, 0.0, 0.0),
                    bbox=BBox(min=(-1, -1, -1), max=(1, 1, 1)),
                )
            ],
        )
        job.progress = 100
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
