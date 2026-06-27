/**
 * Shared types for the frontend <-> backend seam.
 * Source of truth: CONTRACT.md (jointly owned — do not change unilaterally).
 */

export type Vec3 = [number, number, number];

export interface BBox {
  min: Vec3;
  max: Vec3;
}

/** One separated part of the model, positioned in PartCrafter's global canonical frame. */
export interface PartSource {
  url: string;
  title: string;
}

export interface Part {
  part_id: string;
  /** Human label, e.g. "housing". Falls back to "part_0" when PartCrafter gives no name. */
  label: string;
  description?: string;
  confidence?: number;
  source_status?: string;
  sources?: PartSource[];
  visual_evidence?: string;
  unknowns?: string[];
  /** GLB served by the backend under /files/... */
  model_url: string;
  /** Part center in the same canonical frame. Used (with `center`) to compute explode. */
  centroid: Vec3;
  bbox: BBox;
}

export interface Citation {
  claim_id?: string;
  source_type?: string;
  url: string;
  title: string;
  used_for?: string;
  snippet?: string;
}

export interface ManualStep {
  step?: number;
  title?: string;
  description?: string;
  [key: string]: unknown;
}

export interface SnapliiAction {
  id: string;
  type: "manual_card" | "parts_action" | "reward_claim";
  status: string;
  label: string;
  url: string;
  job_id: string;
  created_at: number;
  requires_user_approval: boolean;
  mock?: boolean;
  metadata?: Record<string, unknown>;
}

/** The heart of the contract — everything the viewer needs to render + explode. */
export interface ModelResult {
  kind?: "3d";
  model_id: string;
  source_image_url: string;
  manual_url?: string;
  /** Global center; explode radiates from here. */
  center: Vec3;
  bbox: BBox;
  /** One entry per part, arbitrary count. Length 1 = fused-mesh fallback. */
  parts: Part[];
  object_type?: string;
  likely_model?: string;
  object_summary?: string;
  object_confidence?: number;
  citations?: Citation[];
  steps?: ManualStep[];
  warnings?: string[];
  non_claims?: string[];
  explode_frames?: string[];
  turntable_frames?: string[];
  snaplii_actions?: SnapliiAction[];
}

export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  job_id: string;
  status: JobStatus;
  /** 0–100, best effort. */
  progress: number;
  /** Present only when status === "done". Discriminated by `kind`. */
  result: ModelResult | TwoDResult | null;
  /** Human-readable string when status === "error", else null. */
  error: string | null;
}

/* ----- Agent action protocol (frozen — both sides implement exactly these) ----- */

export type AgentAction =
  | { type: "explode"; factor: number } // set explode slider to factor (0–1)
  | { type: "highlight"; part_id: string } // emphasize one part
  | { type: "isolate"; part_ids: string[] } // show only these parts
  | { type: "focus"; part_id: string } // move camera to frame this part
  | { type: "reset" }; // assembled, all visible, camera home

export type AgentActionType = AgentAction["type"];

export interface AgentRequest {
  model_id: string;
  message: string;
  /** Current viewer state so the agent has context. */
  explode_factor: number;
}

export interface AgentResponse {
  reply: string;
  /** Frontend executes these in order. */
  actions: AgentAction[];
  /** Citations from Google Search grounding. */
  citations?: Citation[];
}

/**
 * 2D-path result — the GMI-deployable pipeline (image model → multi-angle shots
 * → Kling V3 exploded-view video). The frame slider scrubs `video_url`.
 *
 * NOTE: this is a PROPOSED contract addition. CONTRACT.md is jointly owned —
 * agree the shape with the backend before relying on it. `/api/generate` would
 * return either a ModelResult (3D) or a TwoDResult (2D), discriminated by `kind`.
 */
export interface TwoDResult {
  kind: "2d";
  model_id: string;
  source_image_url: string;
  /** Exploded-view sequence; scrub currentTime 0 → duration (0% → 100%). */
  video_url: string;
  /** Optional: total frames, for the frame readout. */
  frame_count?: number;
  /** Optional: the multi-angle input shot URLs. */
  angles?: string[];
}
