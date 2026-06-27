"""PDF manual renderer using reportlab.

Generates a structured PDF modeled after motherboard manual information architecture:
Cover → Contents → Object Overview → Numbered Layout → Exploded View →
360 Preview → Part Cards → Operation Pages → Warnings → Sources
"""

import io
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, black, white, grey
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
        Image as RLImage, ListFlowable, ListItem,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("pdf: reportlab not installed — PDF rendering disabled")


def render_pdf(manual: dict, output_path: str, source_image_path: Optional[str] = None) -> bool:
    """Render manual.json to a PDF file.

    Returns True on success, False on failure.
    """
    if not REPORTLAB_AVAILABLE:
        logger.warning("pdf: reportlab not available — cannot render PDF")
        return False

    try:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("CustomTitle", parent=styles["Title"], fontSize=28, spaceAfter=12)
        heading_style = ParagraphStyle("CustomHeading", parent=styles["Heading1"], fontSize=18, spaceAfter=8, textColor=HexColor("#1a1a2e"))
        subheading_style = ParagraphStyle("CustomSubHeading", parent=styles["Heading2"], fontSize=14, spaceAfter=6, textColor=HexColor("#4a9eff"))
        body_style = ParagraphStyle("CustomBody", parent=styles["Normal"], fontSize=11, spaceAfter=6, leading=16)
        small_style = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, textColor=grey, spaceAfter=4)
        warning_style = ParagraphStyle("Warning", parent=styles["Normal"], fontSize=10, textColor=HexColor("#c0392b"), spaceAfter=4)
        disclaimer_style = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=10, textColor=HexColor("#7f8c8d"), spaceAfter=4, alignment=TA_CENTER)

        story = []

        # === COVER ===
        story.append(Spacer(1, 2 * inch))
        story.append(Paragraph("Generated Visual Manual", title_style))
        story.append(Spacer(1, 0.3 * inch))
        obj = manual.get("object", {})
        story.append(Paragraph(f"Object: {obj.get('type', 'Unknown')}", subheading_style))
        if obj.get("likely_model") and obj["likely_model"] != "unknown":
            story.append(Paragraph(f"Likely model: {obj['likely_model']}", body_style))
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph(f"Generated: {time.strftime('%Y-%m-%d', time.localtime(manual.get('generated_at', time.time())))}", body_style))
        story.append(Spacer(1, 0.5 * inch))
        story.append(Paragraph("AI-generated visual guide, not manufacturer-certified documentation.", disclaimer_style))
        story.append(PageBreak())

        # === TABLE OF CONTENTS ===
        story.append(Paragraph("Contents", heading_style))
        story.append(Spacer(1, 0.2 * inch))
        sections = manual.get("sections", [])
        for i, sec in enumerate(sections, 1):
            story.append(Paragraph(f"{i}. {sec['title']}", body_style))
        story.append(PageBreak())

        # === 1. OBJECT OVERVIEW ===
        story.append(Paragraph("1. Object Overview", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(f"<b>Type:</b> {obj.get('type', 'Unknown')}", body_style))
        if obj.get("likely_model") and obj["likely_model"] != "unknown":
            story.append(Paragraph(f"<b>Likely model:</b> {obj['likely_model']}", body_style))
        story.append(Paragraph(f"<b>Confidence:</b> {obj.get('confidence', 0.0):.0%}", body_style))
        story.append(Paragraph(f"<b>Summary:</b> {obj.get('summary', '')}", body_style))

        # Input images
        images = manual.get("input_images", [])
        if images:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("<b>Input images:</b>", body_style))
            for img in images:
                story.append(Paragraph(f"• {img.get('id', '')} — {img.get('role', '')}", small_style))

        # Try to embed source image
        if source_image_path and os.path.exists(source_image_path):
            try:
                img = RLImage(source_image_path, width=4 * inch, height=3 * inch, kind="proportional")
                story.append(Spacer(1, 0.1 * inch))
                story.append(img)
            except Exception as e:
                logger.warning("pdf: could not embed source image — %s", e)

        story.append(PageBreak())

        # === 2. NUMBERED LAYOUT ===
        story.append(Paragraph("2. Numbered Layout", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        parts = manual.get("parts", [])
        if parts:
            # Legend table
            data = [["No.", "Part", "What it does", "Confidence", "Source"]]
            for p in parts:
                data.append([
                    str(p.get("number", "")),
                    p.get("label", ""),
                    p.get("description", "")[:60],
                    f"{p.get('confidence', 0.0):.0%}",
                    p.get("source_status", "vision_inferred"),
                ])
            table = Table(data, colWidths=[0.5 * inch, 1.5 * inch, 2.5 * inch, 0.8 * inch, 1.2 * inch])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#4a9eff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f9fa"), white]),
            ]))
            story.append(table)
        else:
            story.append(Paragraph("No parts identified.", body_style))
        story.append(PageBreak())

        # === 3. EXPLODED VIEW ===
        story.append(Paragraph("3. Exploded View", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        exploded = next((s for s in sections if s["id"] == "exploded"), {})
        media = exploded.get("media", {})
        if media.get("video"):
            story.append(Paragraph(f"<b>Video:</b> {media['video']}", small_style))
        frames = media.get("frames", [])
        if frames:
            story.append(Paragraph(f"{len(frames)} frames extracted from explosion animation.", body_style))
        else:
            story.append(Paragraph("Exploded view generation was blocked. No frames available.", body_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("⚠ Visual aid only. This is not a true 3D model.", warning_style))
        story.append(PageBreak())

        # === 4. 360-STYLE PREVIEW ===
        story.append(Paragraph("4. 360-Style Preview", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        turntable = next((s for s in sections if s["id"] == "turntable"), {})
        media = turntable.get("media", {})
        if media.get("video"):
            story.append(Paragraph(f"<b>Video:</b> {media['video']}", small_style))
        frames = media.get("frames", [])
        if frames:
            story.append(Paragraph(f"{len(frames)} frames extracted from turntable animation.", body_style))
        else:
            story.append(Paragraph("360 preview generation was blocked. No frames available.", body_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("This is a frame-based preview, not real mesh reconstruction.", disclaimer_style))
        story.append(PageBreak())

        # === 5. PART CARDS ===
        story.append(Paragraph("5. Part Cards", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        for p in parts:
            story.append(Paragraph(f"<b>{p.get('number', '')}. {p.get('label', '')}</b>", subheading_style))
            story.append(Paragraph(f"<b>Description:</b> {p.get('description', '')}", body_style))
            story.append(Paragraph(f"<b>Visual evidence:</b> {p.get('visual_evidence', '')}", body_style))
            story.append(Paragraph(f"<b>Confidence:</b> {p.get('confidence', 0.0):.0%}", body_style))
            story.append(Paragraph(f"<b>Source status:</b> {p.get('source_status', 'vision_inferred')}", body_style))
            unknowns = p.get("unknowns", [])
            if unknowns:
                story.append(Paragraph("<b>Unknowns:</b>", small_style))
                for u in unknowns:
                    story.append(Paragraph(f"• {u}", small_style))
            story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

        # === 6. OPERATION PAGES ===
        story.append(Paragraph("6. Operation Pages", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Step-by-step instructions are generated based on visual analysis.", body_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("⚠ Do not infer exact pinouts or power specifications without official sources.", warning_style))
        story.append(Paragraph("Sections labeled as 'likely' or 'unverified' lack official source support.", body_style))
        story.append(PageBreak())

        # === 7. WARNINGS / NON-CLAIMS ===
        story.append(Paragraph("7. Warnings and Unknowns", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        for w in manual.get("warnings", []):
            story.append(Paragraph(f"⚠ {w}", warning_style))
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("<b>Limitations:</b>", subheading_style))
        for nc in manual.get("non_claims", []):
            story.append(Paragraph(f"• {nc}", body_style))
        story.append(PageBreak())

        # === 8. SOURCES / RECEIPTS ===
        story.append(Paragraph("8. Sources and Confidence Report", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        citations = manual.get("citations", [])
        if citations:
            for c in citations:
                story.append(Paragraph(f"<b>{c.get('title', 'Untitled')}</b>", body_style))
                story.append(Paragraph(f"URL: {c.get('url', '')}", small_style))
                story.append(Paragraph(f"Type: {c.get('source_type', '')} — Used for: {c.get('used_for', '')}", small_style))
                story.append(Spacer(1, 0.1 * inch))
        else:
            story.append(Paragraph("No external sources were found or used.", body_style))

        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("<b>Search queries:</b>", subheading_style))
        for q in manual.get("search_queries", []):
            story.append(Paragraph(f"• {q}", small_style))

        # Build PDF — try with image first, retry without if image causes failure
        try:
            doc.build(story)
        except Exception as img_err:
            if source_image_path:
                logger.warning("pdf: build failed with image — retrying without — %s", img_err)
                # Rebuild story without the image
                story_no_img = [s for s in story if not isinstance(s, RLImage)]
                doc.build(story_no_img)
            else:
                raise

        logger.info("pdf: rendered to %s", output_path)
        return True

    except Exception as e:
        logger.error("pdf: render failed — %s", e)
        return False
