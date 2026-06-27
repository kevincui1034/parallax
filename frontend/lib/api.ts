/**
 * Thin client for the backend API (see CONTRACT.md).
 * Base URL comes from NEXT_PUBLIC_API_BASE (the AgentBox URL).
 */
import type { AgentRequest, AgentResponse, Job, SnapliiAction } from "./contract";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function url(path: string): string {
  return `${API_BASE}${path}`;
}

/** Resolve a (possibly relative) backend URL against the backend base. */
export function fileUrl(modelUrl: string): string {
  if (/^https?:\/\//.test(modelUrl)) return modelUrl;
  return url(modelUrl);
}

/** Kick off generation. `mode` picks the 2D (image→video) or 3D (parts) path. */
export async function startGenerate(
  image: File,
  mode: "2d" | "3d" = "2d",
): Promise<Job> {
  const body = new FormData();
  body.append("image", image);
  body.append("mode", mode);
  const res = await fetch(url("/api/generate"), { method: "POST", body });
  if (!res.ok) throw new Error(`generate failed: ${res.status}`);
  return res.json();
}

/** Fetch a job's current state. */
export async function getJob(jobId: string): Promise<Job> {
  const res = await fetch(url(`/api/jobs/${jobId}`), { cache: "no-store" });
  if (!res.ok) throw new Error(`job fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Poll a job every `intervalMs` until it is `done` or `error`.
 * `onProgress` fires on each tick. Resolves with the terminal job.
 */
export async function pollJob(
  jobId: string,
  onProgress?: (job: Job) => void,
  intervalMs = 1500,
): Promise<Job> {
  for (;;) {
    const job = await getJob(jobId);
    onProgress?.(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** Ask the agent. Returns reply + ordered actions for the viewer to execute. */
export async function askAgent(req: AgentRequest): Promise<AgentResponse> {
  const res = await fetch(url("/api/agent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`agent failed: ${res.status}`);
  return res.json();
}

/** Create a Snaplii action card for a completed manual. */
export async function createSnapliiAction(
  jobId: string,
  actionType: "manual_card" | "parts_action" | "reward_claim",
  label?: string,
): Promise<SnapliiAction> {
  const res = await fetch(url(`/v1/manuals/${jobId}/snaplii/actions`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_type: actionType, label }),
  });
  if (!res.ok) throw new Error(`snaplii action failed: ${res.status}`);
  return res.json();
}

/** Get a specific Snaplii action by ID. */
export async function getSnapliiAction(jobId: string, actionId: string): Promise<SnapliiAction> {
  const res = await fetch(url(`/v1/manuals/${jobId}/snaplii/actions/${actionId}`), {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`snaplii get failed: ${res.status}`);
  return res.json();
}
