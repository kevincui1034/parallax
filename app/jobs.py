"""Job state management for Agent Visual Manual.

manual.json is the source of truth. PDF and HTML are render targets.
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_BLOB_API = "https://blob.vercel-storage.com"
_API_VERSION = "7"


def _blob_token() -> str:
    return os.environ.get("BLOB_READ_WRITE_TOKEN", "")


def _blob_enabled() -> bool:
    return bool(_blob_token())


def _job_key(job_id: str) -> str:
    return f"jobs/{job_id}.json"


def _blob_put_job(job: "JobState") -> None:
    """Persist a job to Vercel Blob (sync)."""
    if not _blob_enabled():
        return
    import json
    key = _job_key(job.job_id)
    headers = {
        "authorization": f"Bearer {_blob_token()}",
        "x-api-version": _API_VERSION,
        "x-content-type": "application/json",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            client.put(f"{_BLOB_API}/{key}", content=json.dumps(job.to_dict()).encode("utf-8"), headers=headers)
    except Exception as e:
        logger.warning("jobs: blob put failed for %s — %s", job.job_id, e)


def _blob_get_job(job_id: str) -> Optional[dict]:
    """Load a job dict from Vercel Blob (sync)."""
    if not _blob_enabled():
        return None
    import json
    key = _job_key(job_id)
    base = os.environ.get("BLOB_BASE_URL", "").rstrip("/")
    headers = {"authorization": f"Bearer {_blob_token()}", "x-api-version": _API_VERSION}
    try:
        with httpx.Client(timeout=30.0) as client:
            url = f"{base}/{key}" if base else None
            if not url:
                resp = client.get(_BLOB_API, params={"prefix": key, "limit": "1"}, headers=headers)
                if resp.status_code >= 400:
                    return None
                blobs = resp.json().get("blobs", [])
                url = next((b.get("url") for b in blobs if b.get("pathname") == key), None) or (
                    blobs[0].get("url") if blobs else None
                )
            if not url:
                return None
            r = client.get(url)
            if r.status_code >= 400:
                return None
            return r.json()
    except Exception as e:
        logger.warning("jobs: blob get failed for %s — %s", job_id, e)
        return None


@dataclass
class InputImage:
    id: str
    url: str  # local file path
    public_url: str  # GMI-hosted URL for API calls
    role: str = "main object photo"
    thumbnail: str = ""


@dataclass
class Part:
    id: str
    number: int
    label: str
    description: str = ""
    visual_evidence: str = ""
    confidence: float = 0.0
    source_status: str = "vision_inferred"  # vision_inferred, search_supported, official
    sources: list[dict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


@dataclass
class Citation:
    claim_id: str
    source_type: str  # official_search_result, vision_inferred, generated_media
    url: str = ""
    title: str = ""
    used_for: str = ""
    snippet: str = ""


@dataclass
class VideoResult:
    mode: str  # "explode" or "turntable"
    video_url: str = ""
    frames: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, processing, completed, blocked
    error: str = ""


@dataclass
class ManualSection:
    id: str
    title: str
    parts: list[str] = field(default_factory=list)  # part ids
    media: dict = field(default_factory=dict)  # video url, frames


@dataclass
class JobState:
    job_id: str
    status: str  # queued, understanding, searching, planning, generating, extracting, rendering, completed, partial, blocked
    progress: int  # 0-100
    simple_message: str
    input_images: list[dict] = field(default_factory=list)
    object_type: str = ""
    likely_model: str = ""
    object_confidence: float = 0.0
    object_summary: str = ""
    parts: list[dict] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    visual_overlay: dict = field(default_factory=dict)
    kling_prompts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    search_results: list[dict] = field(default_factory=list)
    explode: dict = field(default_factory=dict)  # VideoResult as dict
    turntable: dict = field(default_factory=dict)
    manual_json: dict = field(default_factory=dict)
    snaplii_actions: list[dict] = field(default_factory=list)
    artifact_dir: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self):
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobState":
        fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in fields})

    def save(self) -> None:
        _store[self.job_id] = self
        _blob_put_job(self)


# In-memory store (process-local cache; Blob is the durable store on Vercel)
_store: dict[str, JobState] = {}


def create_job() -> JobState:
    job_id = f"manual_{uuid.uuid4().hex[:12]}"
    job = JobState(
        job_id=job_id,
        status="queued",
        progress=0,
        simple_message="Job queued.",
    )
    job.save()
    return job


def get_job(job_id: str) -> Optional[JobState]:
    job = _store.get(job_id)
    if job:
        return job
    data = _blob_get_job(job_id)
    if data:
        job = JobState.from_dict(data)
        _store[job_id] = job
        return job
    return None


def update_job(job_id: str, **kwargs) -> Optional[JobState]:
    job = get_job(job_id)
    if not job:
        return None
    for k, v in kwargs.items():
        if hasattr(job, k):
            setattr(job, k, v)
    job.touch()
    job.save()
    return job
