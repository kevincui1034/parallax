"use client";

import { useEffect, useRef } from "react";

/**
 * 2D frame-scrub viewer.
 *
 * In the real pipeline, a GMI image-generation agent produces the part (and/or
 * multi-angle shots), and a second GMI model turns those into an exploded-view
 * sequence. The navbar slider scrubs that sequence: factor 0 = assembled (first
 * frame), factor 1 = fully exploded (last frame).
 *
 * Until that output exists, this draws a procedural exploded-parts diagram on a
 * canvas so the scrub UX is fully demoable. To swap in real output, replace
 * draw() with either:
 *   - frames[]:  ctx.drawImage(frames[Math.round(factor * (frames.length - 1))])
 *   - video:     video.currentTime = factor * video.duration  (and draw the video)
 */

const ACCENT = "#3ad8ff";

interface Part {
  id: string;
  name: string;
  w: number; // world units
  h: number;
  color: string;
}

// Top → bottom, echoing the 3D demo's single-cylinder assembly.
const PARTS: Part[] = [
  { id: "P-08", name: "Valve Spring", w: 46, h: 26, color: "#7f878f" },
  { id: "P-07", name: "Intake Valve", w: 34, h: 30, color: "#aeb6be" },
  { id: "P-03", name: "Compression Ring", w: 96, h: 16, color: "#5a626b" },
  { id: "P-02", name: "Piston", w: 104, h: 56, color: "#c2cad2" },
  { id: "P-05", name: "Connecting Rod", w: 40, h: 78, color: "#868e96" },
  { id: "P-06", name: "Crank Journal", w: 120, h: 40, color: "#6f777f" },
];

const GAP = 14; // assembled gap between parts (world units)
const SPREAD = 1.35; // how far parts fly apart at factor = 1
const DEPTH_X = 11; // iso extrusion
const DEPTH_Y = 7;

interface Layout {
  home: number; // assembled center Y (world units, 0 = stack center)
  spread: number; // additional Y offset applied * factor
  part: Part;
}

function buildLayout(): { items: Layout[]; extent: number } {
  const totalH = PARTS.reduce((s, p) => s + p.h, 0) + GAP * (PARTS.length - 1);
  let y = -totalH / 2;
  const homes = PARTS.map((p) => {
    const center = y + p.h / 2;
    y += p.h + GAP;
    return center;
  });
  const stackCenter = 0;
  const items: Layout[] = PARTS.map((p, i) => ({
    part: p,
    home: homes[i],
    spread: (homes[i] - stackCenter) * SPREAD,
  }));
  // vertical extent at factor = 1 (for a stable, non-rescaling fit)
  const tops = items.map((it) => it.home + it.spread - it.part.h / 2);
  const bots = items.map((it) => it.home + it.spread + it.part.h / 2);
  const extent = Math.max(...bots) - Math.min(...tops);
  return { items, extent };
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export default function TwoDStage({
  factor,
  active,
  frameCount = 48,
}: {
  factor: number;
  active: boolean;
  frameCount?: number;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const layoutRef = useRef(buildLayout());
  const factorRef = useRef(factor);
  factorRef.current = factor;

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;

    const draw = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const cw = wrap.clientWidth;
      const ch = wrap.clientHeight;
      if (!cw || !ch) return;
      if (canvas.width !== cw * dpr || canvas.height !== ch * dpr) {
        canvas.width = cw * dpr;
        canvas.height = ch * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);

      const f = Math.max(0, Math.min(1, factorRef.current));
      const { items, extent } = layoutRef.current;

      // scale to fit the factor=1 extent (stable while scrubbing)
      const scale = Math.min((ch * 0.82) / extent, (cw * 0.4) / 120);
      const cx = cw * 0.42; // leave room on the right for labels
      const cy = ch / 2;

      // assembly axis
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(cx, cy - (extent * scale) / 2 - 18);
      ctx.lineTo(cx, cy + (extent * scale) / 2 + 18);
      ctx.stroke();
      ctx.setLineDash([]);

      const exploded = f > 0.06;

      items.forEach((it) => {
        const p = it.part;
        const wy = it.home + it.spread * f; // world Y
        const sy = cy + wy * scale;
        const w = p.w * scale;
        const h = p.h * scale;
        const x = cx - w / 2;
        const y = sy - h / 2;

        // iso extrusion (top + right faces)
        ctx.fillStyle = shade(p.color, -0.45);
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + DEPTH_X, y - DEPTH_Y);
        ctx.lineTo(x + w + DEPTH_X, y - DEPTH_Y);
        ctx.lineTo(x + w, y);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = shade(p.color, -0.62);
        ctx.beginPath();
        ctx.moveTo(x + w, y);
        ctx.lineTo(x + w + DEPTH_X, y - DEPTH_Y);
        ctx.lineTo(x + w + DEPTH_X, y + h - DEPTH_Y);
        ctx.lineTo(x + w, y + h);
        ctx.closePath();
        ctx.fill();

        // front face — vertical metallic gradient
        const g = ctx.createLinearGradient(0, y, 0, y + h);
        g.addColorStop(0, shade(p.color, 0.22));
        g.addColorStop(0.5, p.color);
        g.addColorStop(1, shade(p.color, -0.28));
        ctx.fillStyle = g;
        roundRect(ctx, x, y, w, h, 3);
        ctx.fill();
        ctx.strokeStyle = "rgba(0,0,0,0.45)";
        ctx.lineWidth = 1;
        ctx.stroke();

        // leader line + label, fading in as parts separate
        if (exploded) {
          const alpha = Math.min(1, (f - 0.06) / 0.25);
          const lx = cx + (extent * scale) * 0.32 + 30;
          const ly = sy;
          ctx.strokeStyle = `rgba(58,216,255,${0.5 * alpha})`;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x + w + DEPTH_X + 4, sy - DEPTH_Y / 2);
          ctx.lineTo(lx - 8, ly);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font =
            "600 10px var(--font-jbmono, ui-monospace), monospace";
          ctx.textBaseline = "middle";
          ctx.fillStyle = `rgba(58,216,255,${alpha})`;
          ctx.fillText(p.id, lx, ly);
          ctx.fillStyle = `rgba(231,236,241,${0.82 * alpha})`;
          ctx.fillText(p.name, lx + 34, ly);
        }
      });
    };

    const loop = () => {
      draw();
      raf = requestAnimationFrame(loop);
    };
    // only animate while visible; redraw once otherwise
    if (active) {
      loop();
    } else {
      draw();
    }

    const ro = new ResizeObserver(() => draw());
    ro.observe(wrap);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [active]);

  const frameIdx = Math.round(Math.max(0, Math.min(1, factor)) * (frameCount - 1));

  return (
    <div className="pl2-wrap" ref={wrapRef}>
      <canvas className="pl2-canvas" ref={canvasRef} />

      {/* top-left HUD */}
      <div className="pl-hud">
        <div className="pl-hud-name">Exploded Sequence</div>
        <div className="pl-hud-row">
          <span className="pl-badge">{PARTS.length} PARTS</span>
          <span className="pl-axis">2D · FRAME-SCRUB</span>
        </div>
        <div className="pl-hud-help">
          DRAG&nbsp;FRAME&nbsp;SLIDER
          <br />
          0%&nbsp;ASSEMBLED&nbsp;&middot;&nbsp;100%&nbsp;EXPLODED
        </div>
      </div>

      {/* multi-angle input strip */}
      <div className="pl2-angles">
        <span className="pl2-angles-label">MULTI-ANGLE&nbsp;INPUT</span>
        <div className="pl2-angles-row">
          {[0, 1, 2, 3].map((i) => (
            <div className="pl2-angle" key={i} style={{ borderColor: i === 0 ? ACCENT : undefined }}>
              <div className="diamond" />
              <span>{i + 1}</span>
            </div>
          ))}
        </div>
      </div>

      {/* bottom-right frame readout */}
      <div className="pl-readout">
        FRAME&nbsp;<span className="v" style={{ color: ACCENT }}>{String(frameIdx).padStart(2, "0")}</span>
        <span className="v">&nbsp;/&nbsp;{frameCount - 1}</span>
        <br />
        STATE&nbsp;
        <span className="v">{factor > 0.94 ? "EXPLODED" : factor < 0.06 ? "ASSEMBLED" : "IN-MOTION"}</span>
      </div>
    </div>
  );
}

/** Lighten (amt>0) or darken (amt<0) a #rrggbb hex by a 0–1 ratio. */
function shade(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16);
  let r = (n >> 16) & 255;
  let g = (n >> 8) & 255;
  let b = n & 255;
  const t = amt < 0 ? 0 : 255;
  const p = Math.abs(amt);
  r = Math.round((t - r) * p + r);
  g = Math.round((t - g) * p + g);
  b = Math.round((t - b) * p + b);
  return `rgb(${r},${g},${b})`;
}
