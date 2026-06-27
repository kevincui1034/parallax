# Frontend Handoff — Agent Visual Manual

## You do not need

- GMI keys
- Gemini
- Kling
- MCP
- Any backend running

## Build against fixtures first

Use `sample-job-completed.json` as your primary fixture. The production API returns the same shape.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/manuals` | Upload images (multipart), returns `{job_id, status}` |
| GET | `/v1/manuals/{job_id}` | Get job status + all data |
| GET | `/v1/manuals/{job_id}/events` | SSE stream with event names |
| GET | `/v1/manuals/{job_id}/artifacts` | Aggregated artifact URLs |
| GET | `/v1/manuals/{job_id}/artifacts/manual.pdf` | Download PDF |
| GET | `/v1/manuals/{job_id}/artifacts/index.html` | View HTML manual |
| GET | `/v1/manuals/{job_id}/artifacts/manual.json` | Get manual.json |
| GET | `/v1/manuals/{job_id}/artifacts/parts.json` | Get parts list |
| GET | `/v1/manuals/{job_id}/artifacts/overlay.json` | Get overlay labels |
| GET | `/v1/manuals/{job_id}/artifacts/citations.json` | Get citations |
| GET | `/v1/manuals/{job_id}/parts/{part_id}` | Get single part card |
| POST | `/v1/manuals/{job_id}/ask` | Ask a question (form: `question`) |
| POST | `/v1/manuals/{job_id}/export` | Get download URLs |
| POST | `/v1/manuals/{job_id}/validate` | Validate claims |

## SSE Event Names

```
image_uploaded
understanding_image
searching_context
planning_parts
generating_visual
generating_exploded_video
extracting_frames
rendering_html
rendering_pdf
validating_artifact
completed
partial
blocked
```

## Status Enum

```
queued → understanding → searching → planning → generating → extracting → rendering → completed|partial|blocked
```

## Layout

```
LEFT:   uploaded images, generated artifacts list
CENTER: visual manual viewer, frame slider
RIGHT:  agent progress, part cards, warnings, export actions
```

## Visible Actions

```
Upload Image
Make Manual
Show Exploded View
Show 360 Preview
Ask About Part
Download PDF
Open HTML
```

## Do NOT expose

```
MCP, GMI, Gemini, Kling, gpt-image-2, LangSmith, RAG, grounding, proof registry
```

## Overlay Labels

Use `overlay.json` for SVG/HTML label positions. Coordinates are normalized [0-1, 0-1]. Do NOT rely on labels baked into generated images.

## TypeScript Types

See `frontend-types.ts` for complete type definitions.

## Fixtures

- `sample-job-completed.json` — Full successful job
- `sample-job-partial.json` — Video blocked, manual still works
- `sample-job-blocked.json` — Analysis failed
- `sample-manual.json` — The manual.json artifact
