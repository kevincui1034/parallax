"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ParallaxViewer, PartMeta } from "@/lib/parallax-viewer";
import TwoDStage from "@/components/two-d-stage";
import { useSpeech } from "@/lib/use-speech";
import {
  startGenerate as apiGenerate,
  pollJob,
  askAgent,
  fileUrl,
} from "@/lib/api";
import type { AgentAction } from "@/lib/contract";

/* ---- types ---- */
type Mode = "3d" | "2d";
type AppState = "loaded" | "empty" | "generating" | "error";
type AssetType = "multi" | "single" | "error";
type Selected = Pick<PartMeta, "id" | "name" | "note">;

interface CachedPart {
  part_id: string;
  label: string;
  description: string;
  confidence: number;
}

interface Cached2DResult {
  kind: "2d";
  model_id: string;
  source_image_url: string;
  video_url: string;
  frame_count: number;
  explode_frames: string[] | null;
  object_type: string;
  likely_model: string;
  manual_url: string;
  pdf_url: string;
  steps: { id: string; title: string; instruction: string }[];
  object_summary: string;
}

interface Cached3DResult {
  model_id: string;
  source_image_url: string;
  manual_url: string;
  pdf_url: string;
  parts: CachedPart[];
  object_type: string;
  likely_model: string;
  object_summary: string;
  object_confidence: number;
  explode_frames: string[];
  turntable_frames: string[];
  steps: { id: string; title: string; instruction: string }[];
  warnings: string[];
}

interface CachedResult {
  job_id: string;
  status: string;
  progress: number;
  result: Cached2DResult | Cached3DResult;
}

interface Msg {
  role: "agent" | "user";
  text: string;
  actions?: string[];
}

interface Asset {
  id: string;
  name: string;
  type: AssetType;
  tag: string;
}

const ASSETS: Asset[] = [
  { id: "ASSET-01", name: "Single-Cylinder Assembly", type: "multi", tag: "8 PARTS" },
  { id: "ASSET-02", name: "Brake Caliper Body", type: "single", tag: "1 PART" },
  { id: "ASSET-03", name: "Coolant Pump Housing", type: "error", tag: "FAILED" },
  { id: "ASSET-04", name: "Turbo Center Section", type: "multi", tag: "8 PARTS" },
  { id: "ASSET-05", name: "Gear Reduction Set", type: "multi", tag: "8 PARTS" },
];

const ACCENT = "#3ad8ff";
const FRAMES_2D = 48;

const INTRO: Msg = {
  role: "agent",
  text:
    "Model ready — Epson EcoTank L3210. I resolved 4 separable parts from real pipeline analysis. Pick a part on the stage, drag the explode slider, or ask me to isolate, focus, or flag wear surfaces.",
  actions: ["reconstruct() · 4 parts"],
};

const GEN_STEPS_3D: [number, string][] = [
  [0, "Segmenting source image…"],
  [22, "Estimating depth & silhouette…"],
  [44, "Reconstructing part geometry…"],
  [66, "Resolving part boundaries…"],
  [84, "Assigning material & axes…"],
];

const GEN_STEPS_2D: [number, string][] = [
  [0, "Uploading photo…"],
  [10, "Analyzing object with Gemini…"],
  [25, "Identifying parts & components…"],
  [40, "Researching technical context…"],
  [55, "Generating Kling explode video…"],
  [75, "Extracting frames from video…"],
  [85, "Building visual manual…"],
  [95, "Rendering PDF…"],
];

// Claude Code-style rotating spinner verbs — cycled every ~2.5s while generating
const SPINNER_VERBS = [
  "Thinking",
  "Analyzing",
  "Identifying",
  "Researching",
  "Synthesizing",
  "Orchestrating",
  "Generating",
  "Rendering",
  "Extracting",
  "Computing",
  "Crafting",
  "Processing",
  "Percolating",
  "Cogitating",
  "Contemplating",
  "Pondering",
  "Ruminating",
  "Inferring",
  "Concocting",
  "Churning",
  "Hashing",
  "Crunching",
  "Simmering",
  "Brewing",
  "Distilling",
];

// Spinner icon frames (Claude Code-style Unicode animation)
const SPINNER_FRAMES = ["·", "✢", "✳", "✶", "✻", "✽"];
const SPINNER_INTERVAL = 120; // ms per frame
const VERB_INTERVAL = 2500; // ms per verb rotation
const SIM_TICK = 80; // ms per simulated progress tick

/** Render an agent action as a chip label, e.g. isolate([P-07, P-08]). */
function fmtAction(a: AgentAction): string {
  switch (a.type) {
    case "explode":
      return `explode(${a.factor})`;
    case "highlight":
      return `highlight(${a.part_id})`;
    case "isolate":
      return `isolate([${a.part_ids.join(", ")}])`;
    case "focus":
      return `focus(${a.part_id})`;
    case "reset":
      return "reset()";
  }
}

export default function Home() {
  const [mode, setMode] = useState<Mode>("2d");
  const [appState, setAppState] = useState<AppState>("loaded");
  const [modelName, setModelName] = useState("Epson EcoTank L3210");
  const [explode, setExplode] = useState(0);
  const [singlePart, setSinglePart] = useState(false);
  const [partCount, setPartCount] = useState(4);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [progress, setProgress] = useState(0);
  const [genStep, setGenStep] = useState("");
  const [spinnerChar, setSpinnerChar] = useState(SPINNER_FRAMES[0]);
  const [spinnerVerb, setSpinnerVerb] = useState(SPINNER_VERBS[0]);
  const [activeAssetId, setActiveAssetId] = useState("ASSET-01");
  const [errAssetName, setErrAssetName] = useState("");
  const [selected, setSelected] = useState<Selected | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([INTRO]);
  // Real exploded-view video (Kling V3 output) for the 2D tab. null → canvas placeholder.
  const [twoDVideoSrc, setTwoDVideoSrc] = useState<string | null>(null);
  const [twoDFrames, setTwoDFrames] = useState<string[] | null>(null);
  const [twoDSourceImage, setTwoDSourceImage] = useState<string | null>(null);
  const [manualUrl, setManualUrl] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [autoPlaying, setAutoPlaying] = useState(false);
  const [streamSourceImage, setStreamSourceImage] = useState<string | null>(null);

  const viewerRef = useRef<ParallaxViewer | null>(null);
  const stageElRef = useRef<HTMLDivElement | null>(null);
  const logElRef = useRef<HTMLDivElement | null>(null);
  const genTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const thinkTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progAccum = useRef(0);
  const spinnerTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const verbTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const simTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const realProgress = useRef(0);
  const simProgress = useRef(0);
  const verbIdx = useRef(0);
  const frameIdx = useRef(0);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoRaf = useRef<number>(0);
  const autoStart = useRef<number>(0);
  const userInteracting = useRef(false);
  const genAssetRef = useRef<Asset | null>(null);
  const singlePartRef = useRef(false);
  const modeRef = useRef<Mode>("2d");
  const explodeRef = useRef(0);
  const cached3dParts = useRef<{ part_id: string; label: string; description: string }[]>([]);

  useEffect(() => {
    singlePartRef.current = singlePart;
  }, [singlePart]);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  useEffect(() => {
    explodeRef.current = explode;
  }, [explode]);

  // One-time client init from URL/env (can't live in a useState initializer
  // without an SSR hydration mismatch). ?tab=2d|3d deep link + optional sample
  // exploded-view clip (NEXT_PUBLIC_SAMPLE_2D_VIDEO) to exercise the video path.
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    const sample = process.env.NEXT_PUBLIC_SAMPLE_2D_VIDEO;
    /* eslint-disable react-hooks/set-state-in-effect -- intentional one-time client init */
    if (t === "2d" || t === "3d") setMode(t);
    if (sample) setTwoDVideoSrc(sample);
    /* eslint-enable react-hooks/set-state-in-effect */

    // Load cached 2D pipeline result for demo (default landing mode)
    fetch("/demo-cache/result-2d.json")
      .then((r) => r.ok ? r.json() : null)
      .then((data: CachedResult | null) => {
        if (!data) return;
        const r = data.result as Cached2DResult;
        setTwoDVideoSrc(r.video_url || null);
        setTwoDFrames(r.explode_frames ?? null);
        setTwoDSourceImage(r.source_image_url ? fileUrl(r.source_image_url) : null);
        setManualUrl(r.manual_url ? fileUrl(r.manual_url) : null);
        setPdfUrl(r.pdf_url ? fileUrl(r.pdf_url) : null);
        const displayName = r.object_type || r.likely_model || "Analyzed object";
        setModelName(displayName);
        setMsgs([
          {
            role: "agent",
            text: `Loaded real pipeline output for ${displayName}. Drag the FRAME slider — 0% assembled, 100% exploded.${r.manual_url ? " Visual manual available." : ""}`,
            actions: [`kling · ${r.frame_count ?? FRAMES_2D} frames`],
          },
        ]);
      })
      .catch(() => {});
  }, []);

  /* ---- Claude Code-style animated spinner + simulated progress ---- */
  const startSpinner = useCallback(() => {
    // Stop any existing timers
    if (spinnerTimer.current) clearInterval(spinnerTimer.current);
    if (verbTimer.current) clearInterval(verbTimer.current);
    if (simTimer.current) clearInterval(simTimer.current);

    frameIdx.current = 0;
    verbIdx.current = Math.floor(Math.random() * SPINNER_VERBS.length);
    simProgress.current = 0;
    realProgress.current = 0;

    setSpinnerChar(SPINNER_FRAMES[0]);
    setSpinnerVerb(SPINNER_VERBS[verbIdx.current]);

    // Rotate spinner icon frames (~120ms)
    spinnerTimer.current = setInterval(() => {
      frameIdx.current = (frameIdx.current + 1) % SPINNER_FRAMES.length;
      setSpinnerChar(SPINNER_FRAMES[frameIdx.current]);
    }, SPINNER_INTERVAL);

    // Rotate verbs (~2.5s)
    verbTimer.current = setInterval(() => {
      verbIdx.current = (verbIdx.current + 1) % SPINNER_VERBS.length;
      setSpinnerVerb(SPINNER_VERBS[verbIdx.current]);
    }, VERB_INTERVAL);

    // Simulated progress: creep forward with easing, never reaching 95% on its own
    simTimer.current = setInterval(() => {
      // Ease off as we approach the cap so it feels organic
      const cap = 92;
      const remaining = cap - simProgress.current;
      if (remaining > 0.5) {
        // Faster early, slower later — proportional to remaining distance
        const delta = Math.max(0.15, remaining * 0.012 + Math.random() * 0.4);
        simProgress.current = Math.min(cap, simProgress.current + delta);
      }
      // Display the max of simulated and real progress
      const display = Math.max(simProgress.current, realProgress.current);
      setProgress(display);

      // Update gen step label based on display progress
      const steps = modeRef.current === "2d" ? GEN_STEPS_2D : GEN_STEPS_3D;
      let step = steps[0][1];
      for (const [threshold, label] of steps) {
        if (display >= threshold) step = label;
      }
      setGenStep(step);
    }, SIM_TICK);
  }, []);

  const stopSpinner = useCallback(() => {
    if (spinnerTimer.current) { clearInterval(spinnerTimer.current); spinnerTimer.current = null; }
    if (verbTimer.current) { clearInterval(verbTimer.current); verbTimer.current = null; }
    if (simTimer.current) { clearInterval(simTimer.current); simTimer.current = null; }
  }, []);

  useEffect(() => () => stopSpinner(), [stopSpinner]);

  /* ---- idle auto-slide: gently oscillate the 2D slider when user is idle ---- */
  const stopAutoPlay = useCallback(() => {
    if (autoRaf.current) { cancelAnimationFrame(autoRaf.current); autoRaf.current = 0; }
    setAutoPlaying(false);
  }, []);

  const startAutoPlay = useCallback(() => {
    if (autoRaf.current) return; // already running
    setAutoPlaying(true);
    autoStart.current = performance.now();
    const animate = (now: number) => {
      const elapsed = (now - autoStart.current) / 1000; // seconds
      // 6-second period: 3s out, 3s back — gentle and meditative
      const phase = (elapsed % 6) / 6; // 0→1
      // sine wave: 0 at phase 0, 1 at phase 0.5, 0 at phase 1
      const factor = 0.5 - 0.5 * Math.cos(phase * Math.PI * 2);
      // ease the very start so it doesn't jump
      const eased = elapsed < 0.8 ? factor * (elapsed / 0.8) : factor;
      setExplode(eased);
      autoRaf.current = requestAnimationFrame(animate);
    };
    autoRaf.current = requestAnimationFrame(animate);
  }, []);

  const appStateRef = useRef<AppState>("loaded");
  useEffect(() => { appStateRef.current = appState; }, [appState]);

  // Reset idle timer whenever user interacts
  const resetIdle = useCallback(() => {
    userInteracting.current = true;
    stopAutoPlay();
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => {
      userInteracting.current = false;
      // Only auto-play in 2D mode when loaded
      if (modeRef.current === "2d" && appStateRef.current === "loaded") {
        startAutoPlay();
      }
    }, 3000);
  }, [stopAutoPlay, startAutoPlay]);

  // Start/stop auto-play when mode or appState changes
  useEffect(() => {
    if (mode !== "2d" || appState !== "loaded") {
      stopAutoPlay();
      if (idleTimer.current) clearTimeout(idleTimer.current);
    } else {
      // Entering 2D loaded state — start idle timer
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(() => {
        if (modeRef.current === "2d" && appState === "loaded") {
          startAutoPlay();
        }
      }, 3000);
    }
    return () => {
      stopAutoPlay();
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [mode, appState, stopAutoPlay, startAutoPlay]);

  useEffect(() => () => { stopAutoPlay(); if (idleTimer.current) clearTimeout(idleTimer.current); }, [stopAutoPlay]);

  /* ---- 3D viewer lifecycle (kept mounted across tabs) ---- */
  useEffect(() => {
    let disposed = false;
    let viewer: ParallaxViewer | null = null;
    let selectTimer: ReturnType<typeof setTimeout> | undefined;

    (async () => {
      const { ParallaxViewer: VC } = await import("@/lib/parallax-viewer");
      if (disposed || !stageElRef.current) return;
      viewer = new VC(stageElRef.current, {
        accent: ACCENT,
        onPick: (meta) => setSelected(meta),
      });
      viewer.setAutoOrbit(false);
      viewerRef.current = viewer;

      // Apply real cached part metadata + real generated frames
      try {
        const resp = await fetch("/demo-cache/result-3d.json");
        if (resp.ok && !disposed) {
          const cached: CachedResult = await resp.json();
          const r = cached.result as Cached3DResult;
          if (r.parts && r.parts.length > 0) {
            viewer.updatePartMetadata(
              r.parts.map((p) => ({ label: p.label, description: p.description })),
            );
            cached3dParts.current = r.parts.map((p) => ({
              part_id: p.part_id,
              label: p.label,
              description: p.description,
            }));
          }
          // Load real Kling frames into the 3D viewer
          if (r.explode_frames && r.explode_frames.length > 0) {
            const sourceImg = r.source_image_url
              ? fileUrl(r.source_image_url)
              : undefined;
            const parts = r.parts.map((p) => ({
              label: p.label,
              description: p.description,
            }));
            viewer.setFrameSequence(r.explode_frames, sourceImg, parts);
          }
        }
      } catch {
        // fallback to default metadata
      }

      selectTimer = setTimeout(() => {
        if (disposed || !viewer) return;
        if (viewer.hasFrameSequence()) {
          // Real frames are showing — set part context from cached data
          const parts = cached3dParts.current;
          if (parts.length > 0) {
            setSelected({
              id: parts[0].part_id,
              name: parts[0].label,
              note: parts[0].description,
            });
          }
        } else {
          viewer.selectPart("P-02");
          const m = viewer.partList().find((p) => p.id === "P-02");
          if (m) setSelected(m);
        }
      }, 500);
    })();

    return () => {
      disposed = true;
      if (selectTimer) clearTimeout(selectTimer);
      viewer?.dispose();
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (logElRef.current) logElRef.current.scrollTop = logElRef.current.scrollHeight;
  }, [msgs, thinking]);

  useEffect(() => {
    return () => {
      if (genTimer.current) clearInterval(genTimer.current);
      if (thinkTimer.current) clearTimeout(thinkTimer.current);
    };
  }, []);

  const selectById = useCallback((id: string) => {
    const v = viewerRef.current;
    if (!v) return;
    const m = v.partList().find((p) => p.id === id);
    if (m) setSelected(m);
  }, []);

  /* ---- scripted responder: 2D scrub + 3D offline fallback (real agent in handleUserText) ---- */
  const runScriptedAgent = useCallback(
    (text: string) => {
      setThinking(true);
      if (thinkTimer.current) clearTimeout(thinkTimer.current);
      thinkTimer.current = setTimeout(() => {
        const v = viewerRef.current;

        // ----- 2D frame-scrub mode -----
        if (modeRef.current === "2d") {
          const twoD: { match: RegExp; actions: string[]; text: string; run: () => void }[] = [
            {
              match: /(come?s? apart|explode|disassemb|take apart|separate|blow.?up|run.*apart|exploded)/i,
              actions: ["scrub(1.0)"],
              text:
                "Scrubbing to the fully exploded frame — the parts fan out along the assembly axis. Each frame between here and 0% is a generated in-between.",
              run: () => setExplode(1),
            },
            {
              match: /(reset|reassemble|assemble|put.*back|default|collapse|back to)/i,
              actions: ["scrub(0.0)"],
              text: "Back to the assembled frame.",
              run: () => setExplode(0),
            },
            {
              match: /(manual|guide|instructions?|how.*use|read.*manual|view.*manual|open.*manual)/i,
              actions: ["open_manual()"],
              text: manualUrl
                ? "Opening the visual manual in a new tab — it has the full part breakdown, assembly steps, and safety notes."
                : "No manual has been generated yet. Upload a product photo to generate one.",
              run: () => { if (manualUrl) window.open(manualUrl, "_blank"); },
            },
            {
              match: /(explain|sequence|how|parts|what|pipeline)/i,
              actions: [],
              text:
                "This view is a generated exploded sequence: an image model produces the part and its multi-angle shots, then a second model synthesizes the frames between assembled and exploded. The slider scrubs that sequence — 0% assembled, 100% exploded.",
              run: () => {},
            },
          ];
          const intent = twoD.find((i) => i.match.test(text));
          if (intent) {
            intent.run();
            setThinking(false);
            setMsgs((prev) =>
              prev.concat([{ role: "agent", text: intent.text, actions: intent.actions }]),
            );
          } else {
            setThinking(false);
            setMsgs((prev) =>
              prev.concat([
                {
                  role: "agent",
                  text:
                    "In the 2D demo I scrub the generated exploded sequence. Try: “run it apart”, “back to assembled”, or “explain the sequence”.",
                  actions: [],
                },
              ]),
            );
          }
          return;
        }

        // ----- 3D mode -----
        const intents: { match: RegExp; actions: string[]; text: string; run: () => void }[] = [
          {
            match: /(come?s? apart|explode|disassemb|take apart|separate|blow.?up)/i,
            actions: ["reset()", "explode(1.0)"],
            text:
              "Separating all eight parts along the assembly axis. Each part holds its functional position in the stack — drag to orbit and read the layout top to bottom.",
            run: () => {
              v?.reset();
              setExplode(1);
              v?.setExplode(1);
            },
          },
          {
            match: /(connecting rod|conrod|\brod\b)/i,
            actions: ["focus(P-05)", "highlight(P-05)"],
            text:
              "The connecting rod (P-05) converts the piston’s linear travel into rotation at the crank journal. I’ve focused the camera and highlighted it.",
            run: () => {
              v?.clearIsolate();
              v?.focus("P-05");
              selectById("P-05");
            },
          },
          {
            match: /(valve train|valvetrain|isolate the valve|valve)/i,
            actions: ["explode(0.5)", "isolate([P-07, P-08])"],
            text:
              "Isolating the valve train — intake valve (P-07) and its return spring (P-08). Everything else is ghosted so you can read their interface.",
            run: () => {
              setExplode(0.5);
              v?.setExplode(0.5);
              v?.isolate(["P-07", "P-08"]);
              selectById("P-07");
            },
          },
          {
            match: /(wear|fastest|fail|fatigue|friction|stress)/i,
            actions: ["explode(0.4)", "highlight([P-03, P-04])"],
            text:
              "Highest-wear surfaces: the compression ring (P-03) sliding against the bore, and the wrist pin (P-04) under fully reversing load. Both highlighted.",
            run: () => {
              v?.clearIsolate();
              setExplode(0.4);
              v?.setExplode(0.4);
              v?.highlight(["P-03", "P-04"]);
            },
          },
          {
            match: /(reset|reassemble|put.*back|clear|default|collapse)/i,
            actions: ["reset()"],
            text: "Reassembled. View, isolation and selection cleared.",
            run: () => {
              v?.reset();
              setExplode(0);
              setSelected(null);
            },
          },
          {
            match: /(focus|zoom).*(piston)|piston/i,
            actions: ["focus(P-02)", "highlight(P-02)"],
            text:
              "The piston (P-02) transfers combustion pressure to the rod. Focused and highlighted.",
            run: () => {
              v?.clearIsolate();
              v?.focus("P-02");
              selectById("P-02");
            },
          },
        ];

        const intent = intents.find((i) => i.match.test(text));

        if (singlePartRef.current && intent && /explode/i.test(intent.actions.join(" "))) {
          setThinking(false);
          setMsgs((prev) =>
            prev.concat([
              {
                role: "agent",
                text:
                  "This asset resolved as a single part, so there’s nothing to explode or isolate. You can still focus and orbit it.",
                actions: ["explode(0) · no-op"],
              },
            ]),
          );
          return;
        }

        if (intent) {
          intent.run();
          setThinking(false);
          setMsgs((prev) =>
            prev.concat([{ role: "agent", text: intent.text, actions: intent.actions }]),
          );
        } else {
          setThinking(false);
          setMsgs((prev) =>
            prev.concat([
              {
                role: "agent",
                text:
                  "I’m a scripted demo agent acting on the live model. Try: “show how it comes apart”, “isolate the valve train”, “which parts wear fastest?”, or “focus the piston”.",
                actions: [],
              },
            ]),
          );
        }
      }, 520);
    },
    [selectById, manualUrl],
  );

  /* ---- apply a single agent action to the live viewer / frame scrubber ---- */
  const applyAction = useCallback(
    (a: AgentAction) => {
      const v = viewerRef.current;
      switch (a.type) {
        case "explode":
          setExplode(a.factor);
          if (modeRef.current === "3d") v?.setExplode(a.factor);
          break;
        case "highlight":
          if (modeRef.current === "3d") {
            v?.clearIsolate();
            v?.highlight(a.part_id);
            selectById(a.part_id);
          }
          break;
        case "isolate":
          if (modeRef.current === "3d") {
            if (explodeRef.current < 0.3) {
              setExplode(0.5);
              v?.setExplode(0.5);
            }
            v?.isolate(a.part_ids);
            if (a.part_ids[0]) selectById(a.part_ids[0]);
          }
          break;
        case "focus":
          if (modeRef.current === "3d") {
            v?.clearIsolate();
            v?.focus(a.part_id);
            selectById(a.part_id);
          }
          break;
        case "reset":
          v?.reset();
          setExplode(0);
          setSelected(null);
          break;
      }
    },
    [selectById],
  );

  /* ---- send a message: real 3.5 Flash for 3D, scripted scrub for 2D ---- */
  const handleUserText = useCallback(
    (text: string) => {
      setMsgs((prev) => prev.concat([{ role: "user", text }]));
      setDraft("");
      resetIdle();

      // 2D: the agent scrubs the generated sequence (scripted, tailored copy).
      if (modeRef.current === "2d") {
        runScriptedAgent(text);
        return;
      }

      // 3D: real agent via /api/agent (3.5 Flash). Scripted fallback if unreachable.
      setThinking(true);
      (async () => {
        try {
          const res = await askAgent({
            model_id: modelName || "demo",
            message: text,
            explode_factor: explodeRef.current,
          });
          res.actions.forEach(applyAction);
          setThinking(false);
          setMsgs((prev) =>
            prev.concat([
              {
                role: "agent",
                text: res.reply || "…",
                actions: res.actions.map(fmtAction),
              },
            ]),
          );
        } catch {
          runScriptedAgent(text); // backend offline → keep the demo alive
        }
      })();
    },
    [runScriptedAgent, applyAction, modelName, resetIdle],
  );

  /* ---- voice input: mic → live transcript → composer draft ---- */
  // Text already committed (typed or finalized) before the current interim chunk.
  const speechBaseRef = useRef("");
  const onInterim = useCallback((text: string) => {
    const base = speechBaseRef.current;
    setDraft(base ? `${base} ${text}` : text);
  }, []);
  const onFinal = useCallback((text: string) => {
    if (!text) return;
    const base = speechBaseRef.current;
    const next = base ? `${base} ${text}` : text;
    speechBaseRef.current = next;
    setDraft(next);
  }, []);
  const speech = useSpeech({ onInterim, onFinal });
  const { listening: micOn, stop: stopMic } = speech;

  const toggleMic = useCallback(() => {
    if (appState !== "loaded") return;
    // Starting a fresh dictation session: anchor to whatever is in the box now.
    if (!micOn) speechBaseRef.current = draft.trim();
    speech.toggle();
  }, [appState, draft, micOn, speech]);

  const send = useCallback(() => {
    const t = draft.trim();
    if (!t || appState !== "loaded") return;
    stopMic();
    speechBaseRef.current = "";
    handleUserText(t);
  }, [draft, appState, handleUserText, stopMic]);

  const onKey = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    },
    [send],
  );

  /* ---- slider: explode (3D) or frame scrub (2D) ---- */
  const onSlider = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value);
    setExplode(val);
    if (modeRef.current === "3d") viewerRef.current?.setExplode(val);
    resetIdle();
  }, [resetIdle]);

  /* ---- selected-part card (3D only) ---- */
  const focusSel = useCallback(() => {
    const v = viewerRef.current;
    if (v && selected) {
      v.clearIsolate();
      v.focus(selected.id);
    }
  }, [selected]);

  const isolateSel = useCallback(() => {
    const v = viewerRef.current;
    if (v && selected) {
      if (explode < 0.3) {
        setExplode(0.5);
        v.setExplode(0.5);
      }
      v.isolate([selected.id]);
    }
  }, [selected, explode]);

  const clearSel = useCallback(() => {
    const v = viewerRef.current;
    if (v) {
      v.reset();
      setExplode(0);
      setSelected(null);
    }
  }, []);

  /* ---- generation simulation (mode-aware) ---- */
  const finishGenerate = useCallback(
    (a: Asset) => {
      if (modeRef.current === "2d") {
        setAppState("loaded");
        setModelName(a.name);
        setActiveAssetId(a.id);
        setSinglePart(false);
        setExplode(0);
        setSelected(null);
        // Real wiring: a TwoDResult from /api/jobs carries the Kling V3 video.
        // setTwoDVideoSrc(fileUrl(result.video_url));  ← swap in when the backend returns it.
        if (!process.env.NEXT_PUBLIC_SAMPLE_2D_VIDEO) setTwoDVideoSrc(null);
        setMsgs([
          {
            role: "agent",
            text: `Generated an exploded sequence for ${a.name}. Drag the frame slider — 0% assembled, 100% exploded — or ask me to run it apart.`,
            actions: ["synthesize() · 48 frames"],
          },
        ]);
        return;
      }

      const single = a.type === "single";
      const v = viewerRef.current;
      if (v) {
        v.setSinglePart(single);
        v.reset();
      }
      const intro: Msg = single
        ? {
            role: "agent",
            text: `Loaded ${a.name}. This source resolved as a single part — explode and isolate are unavailable, but you can focus and orbit it.`,
            actions: ["reconstruct() · 1 part"],
          }
        : {
            role: "agent",
            text: `Loaded ${a.name} — 8 parts resolved. Ask me to explode it, isolate a subsystem, or flag wear surfaces.`,
            actions: ["reconstruct() · 8 parts"],
          };
      setAppState("loaded");
      setModelName(a.name);
      setActiveAssetId(a.id);
      setSinglePart(single);
      setPartCount(single ? 1 : 8);
      setExplode(0);
      setSelected(null);
      setMsgs([intro]);
      if (!single) {
        setTimeout(() => {
          const vv = viewerRef.current;
          if (vv) {
            vv.selectPart("P-02");
            selectById("P-02");
          }
        }, 240);
      }
    },
    [selectById],
  );

  const startGenerate = useCallback(
    (a: Asset) => {
      if (genTimer.current) clearInterval(genTimer.current);
      progAccum.current = 0;
      genAssetRef.current = a;
      const steps = modeRef.current === "2d" ? GEN_STEPS_2D : GEN_STEPS_3D;
      setAppState("generating");
      setProgress(0);
      setGenStep(steps[0][1]);
      startSpinner();

      genTimer.current = setInterval(() => {
        progAccum.current += 3 + Math.random() * 4;
        const p = progAccum.current;

        if (a.type === "error" && p >= 70) {
          if (genTimer.current) clearInterval(genTimer.current);
          stopSpinner();
          setAppState("error");
          setErrAssetName(a.name);
          setProgress(70);
          return;
        }
        if (p >= 100) {
          if (genTimer.current) clearInterval(genTimer.current);
          stopSpinner();
          setProgress(100);
          finishGenerate(a);
          return;
        }
        let step = steps[0][1];
        for (const st of steps) if (p >= st[0]) step = st[1];
        setProgress(p);
        setGenStep(step);
      }, 95);
    },
    [finishGenerate, startSpinner, stopSpinner],
  );

  /* ---- assets / top-level actions ---- */
  const toggleAssets = useCallback(() => setDrawerOpen((o) => !o), []);
  const onNew = useCallback(() => {
    setAppState("empty");
    setDrawerOpen(false);
  }, []);
  /* ---- real 2D pipeline: upload → /api/generate (mode=2d) → poll → play Kling video ---- */
  const runGenerate2D = useCallback(async (file: File) => {
    if (genTimer.current) clearInterval(genTimer.current);
    setAppState("generating");
    setProgress(0);
    setGenStep("Uploading photo…");
    setErrAssetName(file.name);
    setStreamSourceImage(null);
    startSpinner();
    try {
      const job = await apiGenerate(file, "2d");
      const final = await pollJob(job.job_id, (j) => {
        // Sync real progress — the simulated progress takes the max
        const p = j.progress ?? 0;
        realProgress.current = p;
        // Stream partial results: show source image as soon as available
        if (j.source_image_url) {
          setStreamSourceImage(fileUrl(j.source_image_url));
        }
        // Stream backend step message
        if (j.simple_message) {
          setGenStep(j.simple_message);
        }
        // Stream object type if identified
        if (j.object_type) {
          setModelName(j.object_type);
        }
      });
      // Jump to 100% on completion for a satisfying finish
      stopSpinner();
      setProgress(100);
      setGenStep("Done");
      if (final.status === "error" || !final.result || final.result.kind !== "2d") {
        setErrAssetName(file.name);
        setAppState("error");
        return;
      }
      const r = final.result;
      setTwoDVideoSrc(r.video_url ? fileUrl(r.video_url) : null);
      setTwoDFrames(r.explode_frames ?? null);
      setTwoDSourceImage(r.source_image_url ? fileUrl(r.source_image_url) : null);
      setManualUrl(r.manual_url ? fileUrl(r.manual_url) : null);
      setPdfUrl(r.pdf_url ? fileUrl(r.pdf_url) : null);
      setModelName(r.object_type || file.name.replace(/\.[^.]+$/, ""));
      setActiveAssetId("");
      setSinglePart(false);
      setExplode(0);
      setSelected(null);
      setAppState("loaded");
      setMsgs([
        {
          role: "agent",
          text: `Generated an exploded-view clip from ${file.name}. Drag the FRAME slider — 0% assembled, 100% exploded.${r.manual_url ? " You can also view the full visual manual." : ""}`,
          actions: [`kling · ${r.frame_count ?? FRAMES_2D} frames`],
        },
      ]);
      // Start idle auto-play after a brief delay
      setTimeout(() => resetIdle(), 100);
    } catch {
      stopSpinner();
      setErrAssetName(file.name);
      setAppState("error");
    }
  }, [startSpinner, stopSpinner, resetIdle]);

  /* ---- load cached real pipeline output for demo ---- */
  const loadCachedDemo = useCallback(async () => {
    const mode = modeRef.current;
    const cacheUrl = `/demo-cache/result-${mode}.json`;
    setAppState("generating");
    setProgress(0);
    setGenStep(mode === "2d" ? "Loading cached pipeline…" : "Loading cached analysis…");
    setStreamSourceImage(null);
    startSpinner();

    // Simulate progressive steps while fetching
    const steps = mode === "2d" ? GEN_STEPS_2D : GEN_STEPS_3D;
    const stepTimer = setInterval(() => {
      progAccum.current += 8 + Math.random() * 6;
      const p = Math.min(progAccum.current, 95);
      setProgress(p);
      let step = steps[0][1];
      for (const st of steps) if (p >= st[0]) step = st[1];
      setGenStep(step);
    }, 200);

    try {
      const resp = await fetch(cacheUrl);
      if (!resp.ok) throw new Error(`cache fetch failed: ${resp.status}`);
      const cached: CachedResult = await resp.json();
      // Wait a minimum time for the animation to feel real
      await new Promise((r) => setTimeout(r, 1200));

      clearInterval(stepTimer);
      stopSpinner();
      setProgress(100);
      setGenStep("Done");

      if (mode === "2d") {
        const r = cached.result as Cached2DResult;
        setTwoDVideoSrc(r.video_url || null);
        setTwoDFrames(r.explode_frames ?? null);
        setTwoDSourceImage(r.source_image_url ? fileUrl(r.source_image_url) : null);
        setManualUrl(r.manual_url ? fileUrl(r.manual_url) : null);
        setPdfUrl(r.pdf_url ? fileUrl(r.pdf_url) : null);
        setModelName(r.object_type || r.likely_model || "Analyzed object");
        setActiveAssetId("");
        setSinglePart(false);
        setExplode(0);
        setSelected(null);
        setAppState("loaded");
        setMsgs([
          {
            role: "agent",
            text: `Loaded real pipeline output for ${r.object_type || r.likely_model}. Drag the FRAME slider — 0% assembled, 100% exploded.${r.manual_url ? " Visual manual available." : ""}`,
            actions: [`kling · ${r.frame_count ?? FRAMES_2D} frames`],
          },
        ]);
        setTimeout(() => resetIdle(), 100);
      } else {
        const r = cached.result as Cached3DResult;
        const realParts = r.parts;
        const v = viewerRef.current;
        if (v) {
          v.setSinglePart(false);
          v.reset();
          // Update part metadata to match real analysis
          v.updatePartMetadata(
            realParts.map((p) => ({ label: p.label, description: p.description })),
          );
        }
        const displayName = r.object_type || r.likely_model || "Analyzed object";
        setModelName(displayName);
        setActiveAssetId("");
        setSinglePart(false);
        setPartCount(realParts.length);
        setExplode(0);
        setSelected(null);
        setManualUrl(r.manual_url ? fileUrl(r.manual_url) : null);
        setPdfUrl(r.pdf_url ? fileUrl(r.pdf_url) : null);
        setTwoDSourceImage(r.source_image_url ? fileUrl(r.source_image_url) : null);
        setAppState("loaded");
        setMsgs([
          {
            role: "agent",
            text: `Loaded ${displayName} — ${realParts.length} parts resolved from real analysis. Ask me to explode it, isolate a subsystem, or flag wear surfaces.`,
            actions: [`reconstruct() · ${realParts.length} parts`],
          },
        ]);
        // Select first part after a brief delay
        setTimeout(() => {
          const vv = viewerRef.current;
          if (vv) {
            const firstId = vv.partList()[0]?.id;
            if (firstId) {
              vv.selectPart(firstId);
              selectById(firstId);
            }
          }
        }, 240);
      }
    } catch {
      clearInterval(stepTimer);
      stopSpinner();
      // Fallback to simulated generation
      startGenerate(ASSETS[0]);
    }
  }, [startSpinner, stopSpinner, resetIdle, startGenerate, selectById]);

  const useSample = useCallback(() => loadCachedDemo(), [loadCachedDemo]);
  const onFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      if (modeRef.current === "2d") runGenerate2D(file);
      else loadCachedDemo();
    },
    [runGenerate2D, loadCachedDemo],
  );
  const selectAsset = useCallback(
    (a: Asset) => {
      setDrawerOpen(false);
      if (a.id === activeAssetId && appState === "loaded") return;
      startGenerate(a);
    },
    [activeAssetId, appState, startGenerate],
  );
  const retry = useCallback(() => {
    const a = genAssetRef.current;
    if (a) startGenerate({ ...a, type: "multi" });
  }, [startGenerate]);

  const switchMode = useCallback((m: Mode) => {
    setMode(m);
    setDrawerOpen(false);
  }, []);

  /* ---- derived ---- */
  const is2d = mode === "2d";
  const explodePct = Math.round((explode || 0) * 100);
  const explodeLabel = !is2d && singlePart ? "N/A" : explodePct + "%";
  const sliderDisabled = appState !== "loaded" || (!is2d && singlePart);

  const partCountLabel = singlePart ? "1 PART" : partCount + " PARTS";

  const suggestions = is2d
    ? [
        { label: "Run it apart", text: "Run it apart" },
        { label: "Back to assembled", text: "Back to assembled" },
        { label: "Explain the sequence", text: "Explain the sequence" },
        ...(manualUrl ? [{ label: "Open the manual", text: "__OPEN_MANUAL__" }] : []),
      ]
    : [
        { label: "Show how it comes apart", text: "Show me how this comes apart" },
        { label: "Isolate the valve train", text: "Isolate the valve train" },
        { label: "Which parts wear fastest?", text: "Which parts wear the fastest?" },
        { label: "Reset", text: "Reset the view" },
      ];

  return (
    <div className="pl-app">
      {/* NAVBAR */}
      <header className="pl-nav">
        <div className="pl-brand">
          <div className="pl-diamond" />
          <span className="pl-brand-name">PARALLAX</span>
          <span className="pl-brand-ver">v0.4</span>
        </div>
        <div className="pl-vrule" />

        <div className="pl-seg" role="tablist" aria-label="Demo mode">
          <button
            role="tab"
            aria-selected={!is2d}
            className={`pl-seg-btn${!is2d ? " active" : ""}`}
            onClick={() => switchMode("3d")}
          >
            <span className="dot" /> 3D Demo
          </button>
          <button
            role="tab"
            aria-selected={is2d}
            className={`pl-seg-btn${is2d ? " active" : ""}`}
            onClick={() => switchMode("2d")}
          >
            <span className="dot" /> 2D Demo
          </button>
        </div>

        <button className="pl-btn outline" onClick={onNew}>
          <span className="plus">+</span> Upload / New
        </button>
        <div className="pl-model-tag">
          <span className="k">MODEL</span>
          <span className="v">{modelName}</span>
        </div>

        <div className="pl-spacer" />

        <div className="pl-explode" onMouseDown={() => resetIdle()}>
          <span className="lbl">{is2d ? "FRAME" : "EXPLODE"}</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={explode}
            disabled={sliderDisabled}
            onChange={onSlider}
          />
          <span className="val">{explodeLabel}</span>
          {is2d && autoPlaying && (
            <span className="pl-auto-badge" title="Auto-playing — drag slider to take control">
              <span className="pl-auto-dot" /> auto
            </span>
          )}
        </div>

        {is2d && manualUrl && (
          <button
            className="pl-manual-btn"
            onClick={() => window.open(manualUrl, "_blank")}
            title="Open the visual manual in a new tab"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
            </svg>
            View Manual
          </button>
        )}

        <button
          className={`pl-assets-btn${drawerOpen ? " active" : ""}`}
          onClick={toggleAssets}
        >
          <span className="glyph">
            <span />
            <span />
            <span />
            <span />
          </span>
          Assets
        </button>
      </header>

      {/* BODY */}
      <div className="pl-body">
        {/* LEFT: AGENT */}
        <aside className="pl-agent">
          <div className="pl-agent-head">
            <div className="left">
              <span className="pl-agent-dot" />
              <span className="label">AGENT</span>
            </div>
            <span className="sub">{is2d ? "scrubs sequence" : "acts on model"}</span>
          </div>

          <div className="pl-log" ref={logElRef}>
            {msgs.map((m, i) =>
              m.role === "agent" ? (
                <div className="pl-msg" key={i}>
                  <div className="pl-msg-agent">
                    <div className="pl-agent-label">AGENT</div>
                    <div className="pl-bubble-agent">
                      {m.text}
                      {m.actions && m.actions.length > 0 && (
                        <div className="pl-action-row">
                          {m.actions.map((act, j) => (
                            <span className="pl-action-chip" key={j}>
                              {act}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="pl-msg" key={i}>
                  <div className="pl-bubble-user">{m.text}</div>
                </div>
              ),
            )}
            {thinking && (
              <div className="pl-thinking">
                <span />
                <span />
                <span />
              </div>
            )}
          </div>

          {/* composer */}
          <div className="pl-composer">
            <div className="pl-suggestions">
              {suggestions.map((s, i) => (
                <button
                  className="pl-suggest"
                  key={i}
                  onClick={() => {
                    if (s.text === "__OPEN_MANUAL__" && manualUrl) {
                      window.open(manualUrl, "_blank");
                    } else {
                      handleUserText(s.text);
                    }
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <div className="pl-input-wrap">
              <textarea
                rows={2}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKey}
                placeholder={
                  is2d
                    ? "Scrub the sequence, or ask me to run it apart…"
                    : "Ask about a part, or tell me to isolate / focus / explode…"
                }
              />
              {speech.supported && (
                <button
                  className={`pl-mic${micOn ? " on" : ""}`}
                  onClick={toggleMic}
                  disabled={appState !== "loaded"}
                  aria-label={micOn ? "Stop dictation" : "Start dictation"}
                  aria-pressed={micOn}
                  title={micOn ? "Listening… click to stop" : "Dictate"}
                >
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                    <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
                  </svg>
                </button>
              )}
              <button className="pl-send" onClick={send} aria-label="Send">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M5 12h13M13 6l6 6-6 6" />
                </svg>
              </button>
            </div>
          </div>
        </aside>

        {/* CENTER: STAGE (hosts both 3D and 2D; only the active one is shown) */}
        <main className="pl-stage">
          <div className="pl-grid" />
          <div className="pl-glow" />
          <div className="pl-vignette" />
          <div className="pl-crosshair">
            <div className="v" />
            <div className="h" />
          </div>

          {/* 3D content — kept mounted so the WebGL context survives tab switches */}
          <div style={{ position: "absolute", inset: 0, display: is2d ? "none" : undefined }}>
            <div className="pl-mount" ref={stageElRef} />

            <div className="pl-hud">
              <div className="pl-hud-name">{modelName}</div>
              <div className="pl-hud-row">
                <span className="pl-badge">{partCountLabel}</span>
                <span className="pl-axis">ASM-AXIS&nbsp;Y</span>
              </div>
              <div className="pl-hud-help">
                DRAG&nbsp;ORBIT&nbsp;&middot;&nbsp;SCROLL&nbsp;ZOOM
                <br />
                CLICK&nbsp;INSPECT
              </div>
            </div>

            {singlePart && (
              <div className="pl-single-banner">
                SINGLE PART RESOLVED &middot; EXPLODE UNAVAILABLE
              </div>
            )}

            <div className="pl-readout">
              EXPLODE&nbsp;<span className="v">{explodeLabel}</span>
              <br />
              SEPARATION&nbsp;<span className="v">LINEAR</span>
            </div>

            {selected && (
              <div className="pl-sel">
                <div className="pl-sel-head">
                  <span className="pl-sel-id">{selected.id}</span>
                  <span className="pl-sel-flag">SELECTED</span>
                </div>
                <div className="pl-sel-name">{selected.name}</div>
                <div className="pl-sel-note">{selected.note}</div>
                <div className="pl-sel-actions">
                  <button className="pl-sel-btn" onClick={focusSel}>
                    FOCUS
                  </button>
                  <button className="pl-sel-btn" onClick={isolateSel}>
                    ISOLATE
                  </button>
                  <button className="pl-sel-btn" onClick={clearSel}>
                    RESET
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 2D content */}
          <div style={{ position: "absolute", inset: 0, display: is2d ? undefined : "none" }}>
            <TwoDStage
              factor={explode}
              active={is2d}
              frameCount={twoDFrames?.length ?? FRAMES_2D}
              videoSrc={twoDVideoSrc ?? undefined}
              frames={twoDFrames ?? undefined}
              sourceImageUrl={twoDSourceImage ?? undefined}
              objectName={modelName}
            />
          </div>

          {/* ===== SHARED STATE OVERLAYS ===== */}
          {appState === "empty" && (
            <div className="pl-overlay">
              <div className="pl-dropzone-wrap">
                <label className="pl-dropzone">
                  <input
                    type="file"
                    accept="image/*"
                    onChange={onFile}
                    style={{ display: "none" }}
                  />
                  <div className="diamond" />
                  <div className="title">Drop a product photo</div>
                  <div className="hint">
                    JPG / PNG &middot; single clear object
                    <br />
                    {is2d
                      ? "Parallax generates multi-angle shots & an exploded sequence"
                      : "Parallax segments & reconstructs the parts"}
                  </div>
                </label>
                <div className="pl-cta-row">
                  <button className="pl-cta" onClick={useSample}>
                    {is2d ? "Use sample part" : "Use sample assembly"}
                  </button>
                  <button className="pl-cta-ghost" onClick={toggleAssets}>
                    Browse assets
                  </button>
                </div>
              </div>
            </div>
          )}

          {appState === "generating" && (
            <div className="pl-overlay gen">
              <div className="pl-scan" />
              <div className="pl-gen-wrap">
                {/* Streaming source image preview */}
                {streamSourceImage && (
                  <div className="pl-gen-preview">
                    <img src={streamSourceImage} alt="Uploaded product" />
                    <div className="pl-gen-preview-label">SOURCE</div>
                  </div>
                )}
                {/* Skeleton placeholder for the result */}
                {!streamSourceImage && (
                  <div className="pl-gen-skeleton">
                    <div className="pl-sk-line w60" />
                    <div className="pl-sk-line w40" />
                    <div className="pl-sk-block" />
                    <div className="pl-sk-line w80" />
                    <div className="pl-sk-line w30" />
                  </div>
                )}
                <div className="pl-gen-spinner-row">
                  <span className="pl-gen-icon" key={spinnerChar}>{spinnerChar}</span>
                  <span className="pl-gen-verb" key={spinnerVerb}>{spinnerVerb}…</span>
                </div>
                <div className="pl-gen-label">
                  {is2d ? "SYNTHESIZING" : "RECONSTRUCTING"}
                </div>
                <div className="pl-gen-num">
                  <span className="n">{Math.round(progress)}</span>
                  <span className="pct">%</span>
                </div>
                <div className="pl-gen-bar">
                  <div className="fill" style={{ width: Math.round(progress) + "%" }} />
                </div>
                <div className="pl-gen-step">{genStep}</div>
              </div>
            </div>
          )}

          {appState === "error" && (
            <div className="pl-overlay err">
              <div className="pl-err-wrap">
                <div className="pl-err-icon">!</div>
                <div className="pl-err-title">
                  {is2d ? "Generation failed" : "Reconstruction failed"}
                </div>
                <div className="pl-err-code">ERR_GEOMETRY_UNRESOLVED</div>
                <div className="pl-err-detail">
                  Could not segment a clean part boundary from
                  <br />
                  {errAssetName}. Try a sharper, less occluded photo.
                </div>
                <div className="pl-cta-row">
                  <button className="pl-cta" onClick={retry}>
                    Retry
                  </button>
                  <button className="pl-cta-ghost" onClick={onNew}>
                    Choose another
                  </button>
                </div>
              </div>
            </div>
          )}
        </main>

        {/* ASSETS DRAWER (overlay) */}
        <div
          className="pl-drawer"
          style={{ transform: `translateX(${drawerOpen ? "0" : "-340px"})` }}
        >
          <div className="pl-drawer-head">
            <div className="left">
              <span className="label">ASSETS</span>
              <span className="count">{ASSETS.length} resolved</span>
            </div>
            <button
              className="pl-drawer-close"
              onClick={toggleAssets}
              aria-label="Close assets"
            >
              ×
            </button>
          </div>
          <div className="pl-drawer-grid">
            {ASSETS.map((a) => {
              const active = a.id === activeAssetId && appState === "loaded";
              const isErr = a.type === "error";
              const tagColor = isErr
                ? "#c87268"
                : a.type === "single"
                  ? "#d8a04a"
                  : "var(--txt2)";
              const tagBorder = isErr
                ? "rgba(200,114,104,.4)"
                : a.type === "single"
                  ? "rgba(216,160,74,.4)"
                  : "var(--line)";
              return (
                <button
                  className="pl-asset"
                  key={a.id}
                  onClick={() => selectAsset(a)}
                  style={{ borderColor: active ? ACCENT : "var(--line)" }}
                >
                  <div className="pl-asset-thumb">
                    <div
                      className="diamond"
                      style={{ borderColor: active ? ACCENT : "rgba(255,255,255,.22)" }}
                    />
                    {active && <span className="pl-asset-active-dot" />}
                  </div>
                  <div className="pl-asset-meta">
                    <div className="pl-asset-id">{a.id}</div>
                    <div className="pl-asset-name">{a.name}</div>
                    <span
                      className="pl-asset-tag"
                      style={{ color: tagColor, border: `1px solid ${tagBorder}` }}
                    >
                      {a.tag}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
          <div className="pl-drawer-foot">
            <button className="pl-drawer-new" onClick={onNew}>
              <span className="plus">+</span> Upload new photo
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
