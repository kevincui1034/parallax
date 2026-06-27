"""Build artifact node — create the full manual artifact bundle.

Produces:
  manual-artifacts/
    job_<id>/
      source/          — copied input images
      analysis/        — intermediate JSON files
      generated/       — video URLs (if available)
      frames/          — extracted frames
      guide/           — manual.json, manual.pdf, index.html, overlay.json, parts.json, citations.json
      proof/           — receipt.json
"""

import json
import logging
import os
import shutil
import time

from ..config import load_settings
from ..manual import build_manual_json, save_manual_json
from ..pdf import render_pdf

logger = logging.getLogger(__name__)


def _build_manual_html(manual: dict) -> str:
    """Generate a self-contained HTML visual manual from manual.json."""
    parts = manual.get("parts", [])
    warnings = manual.get("warnings", [])
    non_claims = manual.get("non_claims", [])
    obj = manual.get("object", {})
    object_type = obj.get("type", "unknown")
    object_summary = obj.get("summary", "")
    likely_model = obj.get("likely_model", "unknown")
    citations = manual.get("citations", [])

    sections = manual.get("sections", [])
    explode_section = next((s for s in sections if s["id"] == "exploded"), {})
    turntable_section = next((s for s in sections if s["id"] == "turntable"), {})
    explode_frames = explode_section.get("media", {}).get("frames", [])
    turntable_frames = turntable_section.get("media", {}).get("frames", [])

    parts_html = ""
    for p in parts:
        confidence_pct = int(p.get("confidence", 0) * 100)
        sources_html = ""
        for s in p.get("sources", []):
            sources_html += f'<div class="part-source"><a href="{s.get("url", "")}">{s.get("title", "Source")}</a></div>'
        unknowns_html = ""
        for u in p.get("unknowns", []):
            unknowns_html += f"<li>{u}</li>"
        parts_html += f"""
        <div class="part-card" id="part-{p.get('id', '')}">
          <div class="part-number">#{p.get('number', '')}</div>
          <div class="part-label">{p.get('label', p.get('id', 'Unknown'))}</div>
          <div class="part-confidence">Confidence: {confidence_pct}% — {p.get('source_status', 'vision_inferred')}</div>
          <div class="part-desc">{p.get('description', '')}</div>
          <div class="part-evidence"><small>Visual evidence: {p.get('visual_evidence', '')}</small></div>
          {sources_html}
          {f'<div class="part-unknowns"><small>Unknowns:</small><ul>{unknowns_html}</ul></div>' if unknowns_html else ''}
        </div>"""

    warnings_html = "".join(f"<li>{w}</li>" for w in warnings)
    non_claims_html = "".join(f"<li>{n}</li>" for n in non_claims)
    citations_html = ""
    for c in citations:
        citations_html += f'<div class="citation"><a href="{c.get("url", "")}">{c.get("title", "Source")}</a><small>{c.get("used_for", "")}</small></div>'

    explode_frames_json = json.dumps(explode_frames)
    turntable_frames_json = json.dumps(turntable_frames)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Visual Manual — {object_type}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
h1 {{ font-size: 28px; margin-bottom: 4px; }}
h2 {{ font-size: 20px; margin: 24px 0 12px; }}
.summary {{ color: #888; margin-bottom: 8px; }}
.model {{ color: #4a9eff; font-size: 14px; margin-bottom: 24px; }}
.viewer {{ background: #1a1d27; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
.viewer img {{ width: 100%; max-height: 400px; object-fit: contain; border-radius: 8px; }}
.slider {{ width: 100%; margin: 12px 0; accent-color: #4a9eff; }}
.mode-tabs {{ display: flex; gap: 8px; margin-bottom: 16px; }}
.mode-tab {{ padding: 8px 16px; border-radius: 8px; border: 1px solid #333; cursor: pointer; background: #1a1d27; color: #888; }}
.mode-tab.active {{ background: #4a9eff; color: white; border-color: #4a9eff; }}
.parts {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.part-card {{ background: #1a1d27; border-radius: 8px; padding: 16px; }}
.part-number {{ font-size: 12px; color: #4a9eff; font-weight: 700; }}
.part-label {{ font-weight: 600; font-size: 16px; margin: 4px 0; }}
.part-confidence {{ font-size: 12px; color: #4a9eff; margin-bottom: 8px; }}
.part-desc {{ font-size: 14px; color: #aaa; margin-bottom: 8px; }}
.part-evidence {{ font-size: 12px; color: #666; }}
.part-source {{ font-size: 12px; margin: 4px 0; }}
.part-source a {{ color: #4a9eff; }}
.part-unknowns {{ margin-top: 8px; }}
.part-unknowns ul {{ padding-left: 16px; }}
.part-unknowns li {{ font-size: 11px; color: #c0a0a0; }}
.warnings {{ background: #2a1a1a; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.warnings ul, .non-claims ul {{ padding-left: 20px; }}
.warnings li, .non-claims li {{ font-size: 13px; color: #c0a0a0; margin: 4px 0; }}
.non-claims {{ background: #1a1a2a; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.citations {{ margin-bottom: 24px; }}
.citation {{ padding: 8px 0; border-bottom: 1px solid #1a1d27; }}
.citation a {{ color: #4a9eff; text-decoration: none; }}
.citation small {{ display: block; color: #666; }}
.toc {{ background: #1a1d27; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
.toc a {{ color: #4a9eff; text-decoration: none; }}
.toc li {{ margin: 4px 0; }}
</style>
</head>
<body>
<div class="container">
  <h1>Generated Visual Manual</h1>
  <p class="summary">{object_summary}</p>
  <p class="model">Type: {object_type} | Model: {likely_model} | Confidence: {obj.get('confidence', 0.0):.0%}</p>

  <div class="toc">
    <strong>Contents</strong>
    <ol>
      <li><a href="#viewer">360 Preview / Exploded View</a></li>
      <li><a href="#parts">Part Cards</a></li>
      <li><a href="#warnings">Warnings</a></li>
      <li><a href="#sources">Sources</a></li>
    </ol>
  </div>

  <div class="viewer" id="viewer">
    <div class="mode-tabs">
      <div class="mode-tab active" onclick="switchMode('turntable')">360 Preview</div>
      <div class="mode-tab" onclick="switchMode('explode')">Exploded View</div>
    </div>
    <img id="frame-img" src="" alt="Frame viewer"/>
    <input type="range" class="slider" id="frame-slider" min="0" max="0" value="0" oninput="updateFrame()"/>
    <div id="frame-info">Frame 0 / 0</div>
  </div>

  <h2 id="parts">Part Cards</h2>
  <div class="parts">{parts_html}</div>

  <h2 id="warnings">Warnings and Limitations</h2>
  <div class="warnings">
    <strong>Warnings</strong>
    <ul>{warnings_html}</ul>
  </div>
  <div class="non-claims">
    <strong>Limitations</strong>
    <ul>{non_claims_html}</ul>
  </div>

  <h2 id="sources">Sources and Citations</h2>
  <div class="citations">
    {citations_html if citations_html else '<p style="color:#666">No external sources were found.</p>'}
  </div>
</div>

<script>
const explodeFrames = {explode_frames_json};
const turntableFrames = {turntable_frames_json};
let currentMode = 'turntable';
let currentFrames = turntableFrames;

function switchMode(mode) {{
  currentMode = mode;
  currentFrames = mode === 'explode' ? explodeFrames : turntableFrames;
  document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  const slider = document.getElementById('frame-slider');
  slider.max = Math.max(currentFrames.length - 1, 0);
  slider.value = 0;
  updateFrame();
}}

function updateFrame() {{
  const slider = document.getElementById('frame-slider');
  const img = document.getElementById('frame-img');
  const info = document.getElementById('frame-info');
  const idx = parseInt(slider.value);
  if (currentFrames.length > 0) {{
    img.src = currentFrames[idx];
    info.textContent = `Frame ${{idx + 1}} / ${{currentFrames.length}}`;
  }} else {{
    img.src = '';
    info.textContent = 'No frames available';
  }}
}}

const viewer = document.querySelector('.viewer');
viewer.addEventListener('mousemove', (e) => {{
  if (currentFrames.length === 0) return;
  const rect = viewer.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const slider = document.getElementById('frame-slider');
  slider.value = Math.round(x * (currentFrames.length - 1));
  updateFrame();
}});

if (turntableFrames.length > 0) {{
  document.getElementById('frame-slider').max = turntableFrames.length - 1;
  updateFrame();
}}
</script>
</body>
</html>"""


async def build_artifact(state: dict) -> dict:
    """Build the full manual artifact bundle."""
    settings = load_settings()
    job_id = state.get("job_id", "unknown")

    state["status"] = "rendering"
    state["progress"] = 75
    state["simple_message"] = "Building visual manual artifact..."

    artifact_dir = os.path.join(settings.upload_dir, "manual-artifacts", job_id)
    for subdir in ["source", "analysis", "generated", "frames/exploded", "frames/turntable", "guide", "proof"]:
        os.makedirs(os.path.join(artifact_dir, subdir), exist_ok=True)

    # Copy source images
    input_images = state.get("input_images", [])
    for img in input_images:
        src_path = img.get("url", "").replace("file://", "")
        if os.path.exists(src_path):
            dst = os.path.join(artifact_dir, "source", os.path.basename(src_path))
            try:
                shutil.copy2(src_path, dst)
            except Exception:
                pass

    # Save analysis files
    analysis = {
        "image-understanding.json": {
            "object_type": state.get("object_type", ""),
            "likely_model": state.get("likely_model", ""),
            "object_confidence": state.get("object_confidence", 0),
            "object_summary": state.get("object_summary", ""),
        },
        "search-context.json": {
            "queries": state.get("search_queries", []),
            "results": state.get("search_results", []),
        },
        "part-plan.json": {
            "parts": state.get("parts", []),
        },
        "confidence-report.json": {
            "object_confidence": state.get("object_confidence", 0),
            "parts": [{"id": p.get("id"), "confidence": p.get("confidence", 0), "source_status": p.get("source_status", "vision_inferred")} for p in state.get("parts", [])],
        },
    }
    for fname, data in analysis.items():
        with open(os.path.join(artifact_dir, "analysis", fname), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # Save generated media info
    explode = state.get("explode", {})
    turntable = state.get("turntable", {})
    with open(os.path.join(artifact_dir, "generated", "media.json"), "w", encoding="utf-8") as f:
        json.dump({"explode": explode, "turntable": turntable}, f, indent=2)

    # Build and save manual.json
    manual_path = save_manual_json(state, artifact_dir)
    with open(manual_path, "r", encoding="utf-8") as f:
        manual = json.load(f)
    state["manual_json"] = manual

    # Render HTML
    html = _build_manual_html(manual)
    html_path = os.path.join(artifact_dir, "guide", "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Render PDF
    source_image_path = ""
    if input_images:
        src = input_images[0].get("url", "").replace("file://", "")
        if os.path.exists(src):
            source_image_path = src

    pdf_path = os.path.join(artifact_dir, "guide", "manual.pdf")
    pdf_ok = render_pdf(manual, pdf_path, source_image_path)

    # Save proof receipt
    receipt = {
        "job_id": job_id,
        "created_at": time.time(),
        "status": state.get("status", ""),
        "artifact_dir": artifact_dir,
        "manual_json": manual_path,
        "html": html_path,
        "pdf": pdf_path if pdf_ok else None,
        "pdf_rendered": pdf_ok,
        "parts_count": len(state.get("parts", [])),
        "citations_count": len(state.get("citations", [])),
        "explode_status": explode.get("status", "blocked"),
        "turntable_status": turntable.get("status", "blocked"),
    }
    with open(os.path.join(artifact_dir, "proof", "receipt.json"), "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    state["artifact_dir"] = artifact_dir
    state["progress"] = 90
    state["simple_message"] = "Visual manual built."

    logger.info("build_artifact: bundle written to %s (pdf=%s)", artifact_dir, pdf_ok)
    return state
