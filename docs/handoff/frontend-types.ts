// Agent Visual Manual — Frontend Types
// These types match the API responses from the backend.

export type ManualJobStatus =
  | 'queued'
  | 'understanding'
  | 'searching'
  | 'planning'
  | 'generating'
  | 'generating_exploded_video'
  | 'extracting'
  | 'rendering'
  | 'rendering_pdf'
  | 'validating'
  | 'completed'
  | 'partial'
  | 'blocked'

export type SSEEventName =
  | 'image_uploaded'
  | 'understanding_image'
  | 'searching_context'
  | 'planning_parts'
  | 'generating_visual'
  | 'generating_exploded_video'
  | 'extracting_frames'
  | 'rendering_html'
  | 'rendering_pdf'
  | 'validating_artifact'
  | 'completed'
  | 'partial'
  | 'blocked'

export interface ManualJob {
  job_id: string
  status: ManualJobStatus
  progress: number
  simple_message: string
  input_images: InputImage[]
  object_type: string
  likely_model: string
  object_confidence: number
  object_summary: string
  parts: ManualPart[]
  sections: ManualSection[]
  steps: ManualStep[]
  visual_overlay: VisualOverlay
  warnings: string[]
  non_claims: string[]
  citations: Citation[]
  explode: VideoResult
  turntable: VideoResult
  manual_json: ManualJSON | null
  artifact_dir: string
}

export interface InputImage {
  id: string
  url: string
  role: string
}

export interface ManualPart {
  id: string
  number: number
  label: string
  function: string
  description: string
  visual_evidence: string
  confidence: number
  source_status: 'vision_inferred' | 'search_supported' | 'official'
  sources: PartSource[]
  unknowns: string[]
  warnings: string[]
}

export interface PartSource {
  url: string
  title: string
}

export interface ManualSection {
  id: string
  title: string
  parts?: string[]
  media?: {
    video: string
    frames: string[]
    status: string
  }
}

export interface ManualStep {
  id: string
  title: string
  instruction: string
  part_ids: string[]
  confidence: number
}

export interface VisualOverlay {
  labels: OverlayLabel[]
}

export interface OverlayLabel {
  part_id: string
  number: number
  label: string
  anchor: [number, number]
  label_position: [number, number]
}

export interface Citation {
  id?: string
  claim_id: string
  source_type: string
  url: string
  title: string
  used_for: string
  snippet?: string
}

export interface VideoResult {
  video_url: string
  frames: string[]
  status: 'pending' | 'processing' | 'completed' | 'blocked'
  error?: string
}

export interface ManualJSON {
  schema_version: number
  job_id: string
  title: string
  status: string
  generated_at: number
  object: {
    type: string
    likely_name: string
    likely_model: string
    confidence: number
    summary: string
  }
  input_images: InputImage[]
  parts: ManualPart[]
  sections: ManualSection[]
  steps: ManualStep[]
  visual_overlay: VisualOverlay
  warnings: string[]
  non_claims: string[]
  citations: Citation[]
  artifacts: {
    html_url: string
    pdf_url: string
    exploded_frames_url: string
    turntable_frames_url: string
  }
}

export interface ArtifactsResponse {
  job_id: string
  manual_json_url: string
  html_url: string
  pdf_url: string
  parts_json_url: string
  exploded_frames: string[]
  turntable_frames: string[]
  status: string
}

export interface AskResponse {
  job_id: string
  question: string
  answer: string
  part_ids: string[]
  citations: Citation[]
  warnings: string[]
  manual_context: {
    object_type: string
    parts_count: number
  }
}

export interface ValidateResponse {
  job_id: string
  verdict: 'PASS' | 'WARNING' | 'FAIL'
  unsupported_claims: Array<{ part_id: string; claim: string; reason: string }>
  missing_citations: Array<{ part_id: string; reason: string }>
  unsafe_claims: Array<{ part_id: string; claim: string; reason: string }>
  missing_warnings: string[]
  parts_checked: number
  citations_count: number
}
