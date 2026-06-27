"""
Pydantic models for the frontend <-> backend seam.
Source of truth: CONTRACT.md (jointly owned — do not change unilaterally).
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

Vec3 = tuple[float, float, float]


class BBox(BaseModel):
    min: Vec3
    max: Vec3


class Part(BaseModel):
    part_id: str
    label: str  # fallback "part_0" if PartCrafter gives no name
    model_url: str  # served under /files/...
    centroid: Vec3  # same canonical frame as `center`
    bbox: BBox


class ModelResult(BaseModel):
    kind: Literal["3d"] = "3d"
    model_id: str
    source_image_url: str
    center: Vec3  # explode radiates from here
    bbox: BBox
    parts: list[Part]  # one entry per part; length 1 = fused-mesh fallback


class TwoDResult(BaseModel):
    """2D path (GMI-deployable): image -> multi-angle -> Kling exploded video.
    The frontend frame slider scrubs `video_url` (0% assembled, 100% exploded)."""

    kind: Literal["2d"] = "2d"
    model_id: str
    source_image_url: str
    video_url: str
    frame_count: Optional[int] = None
    angles: list[str] = []  # multi-angle input shot URLs


GenResult = Union[ModelResult, TwoDResult]

JobStatus = Literal["queued", "running", "done", "error"]


class Job(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0  # 0–100, best effort
    # present only when status == "done"; discriminated by `kind`
    result: Optional[GenResult] = None
    error: Optional[str] = None


# ----- Agent action protocol (frozen — both sides implement exactly these) -----


class ExplodeAction(BaseModel):
    type: Literal["explode"]
    factor: float = Field(ge=0, le=1)


class HighlightAction(BaseModel):
    type: Literal["highlight"]
    part_id: str


class IsolateAction(BaseModel):
    type: Literal["isolate"]
    part_ids: list[str]


class FocusAction(BaseModel):
    type: Literal["focus"]
    part_id: str


class ResetAction(BaseModel):
    type: Literal["reset"]


AgentAction = Union[
    ExplodeAction, HighlightAction, IsolateAction, FocusAction, ResetAction
]


class AgentRequest(BaseModel):
    model_id: str
    message: str
    explode_factor: float = 0.0  # current viewer state, for agent context


class AgentResponse(BaseModel):
    reply: str
    actions: list[AgentAction] = []  # frontend executes these in order
