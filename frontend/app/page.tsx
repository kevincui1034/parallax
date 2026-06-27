"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ParallaxViewer, PartMeta } from "@/lib/parallax-viewer";
import TwoDStage from "@/components/two-d-stage";
import { startGenerate, pollJob, askAgent, createSnapliiAction, getSnapliiAction, fileUrl, API_BASE } from "@/lib/api";
import type { Job, ModelResult, Part, Citation, SnapliiAction } from "@/lib/contract";

/* ---- types ---- */
type Mode = "3d" | "2d" | "manual";
type AppState = "loaded" | "empty" | "generating" | "error";
type Selected = Pick<PartMeta, "id" | "name" | "note">;

interface Msg {
  role: "agent" | "user";
  text: string;
  actions?: string[];
  citations?: Citation[];
}

const ACCENT = "#3ad8ff";
const FRAMES_2D = 48;

const INTRO: Msg = {
  role: "agent",
  text:
    "Upload a product photo and I'll analyze it with Gemini vision + Google Search grounding. I'll identify parts, generate a visual manual, and answer your questions about the object.",
  actions: ["ready - awaiting upload"],
};

const GEN_STEPS: [number, string][] = [
  [0, "Uploading image..."],
  [10, "Analyzing with Gemini Vision..."],
  [30, "Searching with Google Search grounding..."],
  [50, "Planning parts & structure..."],
  [70, "Generating visual overlays..."],
  [85, "Building manual.json..."],
  [95, "Rendering artifacts..."],
];

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
  const [mode, setMode] = useState<Mode>("3d");
  const [appState, setAppState] = useState<AppState>("empty");
  const [modelName, setModelName] = useState("No model loaded");
  const [explode, setExplode] = useState(0);
  const [singlePart, setSinglePart] = useState(false);
  const [partCount, setPartCount] = useState(0);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [progress, setProgress] = useState(0);
  const [genStep, setGenStep] = useState("");
  const [errAssetName, setErrAssetName] = useState("");
  const [selected, setSelected] = useState<Selected | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([INTRO]);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [currentModelId, setCurrentModelId] = useState<string | null>(null);

  // Enriched data from backend
  const [sourceImageUrl, setSourceImageUrl] = useState<string>("");
  const [manualUrl, setManualUrl] = useState<string>("");
  const [allParts, setAllParts] = useState<Part[]>([]);
  const [allCitations, setAllCitations] = useState<Citation[]>([]);
  const [objectSummary, setObjectSummary] = useState<string>("");
  const [objectType, setObjectType] = useState<string>("");
  const [likelyModel, setLikelyModel] = useState<string>("");
  const [explodeFrames, setExplodeFrames] = useState<string[]>([]);
  const [turntableFrames, setTurntableFrames] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [showPartsPanel, setShowPartsPanel] = useState(false);
  const [snapliiActions, setSnapliiActions] = useState<SnapliiAction[]>([]);

  const viewerRef = useRef<ParallaxViewer | null>(null);
  const stageElRef = useRef<HTMLDivElement | null>(null);
  const logElRef = useRef<HTMLDivElement | null>(null);
  const thinkTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const singlePartRef = useRef(false);
  const modeRef = useRef<Mode>("3d");
  const explodeRef = useRef(0);

  useEffect(() => {
    singlePartRef.current = singlePart;
  }, [singlePart]);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  useEffect(() => {
    explodeRef.current = explode;
  }, [explode]);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "2d" || t === "3d" || t === "manual") setMode(t);
  }, []);

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
        onPick: (meta: PartMeta | null) => { if (meta) setSelected(meta); },
      });
      viewer.setAutoOrbit(false);
      viewerRef.current = viewer;
      selectTimer = setTimeout(() => {
        if (disposed || !viewer) return;
        viewer.selectPart("P-02");
        const m = viewer.partList().find((p) => p.id === "P-02");
        if (m) setSelected(m);
      }, 220);
    })();

    return () => {
      disposed = true;
      if (selectTimer) clearTimeout(selectTimer);
      viewer?.dispose();
    };
  }, []);

  useEffect(() => {
    if (logElRef.current) logElRef.current.scrollTop = logElRef.current.scrollHeight;
  }, [msgs, thinking]);

  useEffect(() => {
    return () => {
      if (thinkTimer.current) clearTimeout(thinkTimer.current);
    };
  }, []);

  const selectById = useCallback((id: string) => {
    const v = viewerRef.current;
    if (!v) return;
    const m = v.partList().find((p) => p.id === id);
    if (m) setSelected(m);
  }, []);

  /* ---- real agent: calls backend /api/agent (Gemini-powered) ---- */
  const handleUserText = useCallback(
    async (text: string) => {
      setMsgs((prev: Msg[]) => prev.concat([{ role: "user", text }]));
      setDraft("");
      setThinking(true);
      if (thinkTimer.current) clearTimeout(thinkTimer.current);

      try {
        if (!currentModelId) {
          setThinking(false);
          setMsgs((prev: Msg[]) =>
            prev.concat([
              { role: "agent", text: "Please upload an image first so I can analyze it.", actions: [] },
            ]),
          );
          return;
        }

        const res = await askAgent({
          model_id: currentModelId,
          message: text,
          explode_factor: explode,
        });

        const v = viewerRef.current;
        const actionLabels: string[] = [];

        for (const action of res.actions) {
          switch (action.type) {
            case "explode":
              actionLabels.push(`explode(${action.factor})`);
              setExplode(action.factor);
              v?.setExplode(action.factor);
              break;
            case "highlight":
              actionLabels.push(`highlight(${action.part_id})`);
              v?.highlight([action.part_id]);
              selectById(action.part_id);
              break;
            case "isolate":
              actionLabels.push(`isolate([${action.part_ids.join(", ")}])`);
              if (explode < 0.3) {
                setExplode(0.5);
                v?.setExplode(0.5);
              }
              v?.isolate(action.part_ids);
              if (action.part_ids[0]) selectById(action.part_ids[0]);
              break;
            case "focus":
              actionLabels.push(`focus(${action.part_id})`);
              v?.clearIsolate();
              v?.focus(action.part_id);
              selectById(action.part_id);
              break;
            case "reset":
              actionLabels.push("reset()");
              v?.reset();
              setExplode(0);
              setSelected(null);
              break;
          }
        }

        setThinking(false);
        setMsgs((prev: Msg[]) =>
          prev.concat([{ role: "agent", text: res.reply, actions: actionLabels, citations: res.citations || [] }]),
        );
      } catch (e) {
        setThinking(false);
        setMsgs((prev: Msg[]) =>
          prev.concat([
            { role: "agent", text: `Error contacting agent: ${e}`, actions: [] },
          ]),
        );
      }
    },
    [selectById, currentModelId, explode],
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
    [runScriptedAgent, applyAction, modelName],
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
  }, []);

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

  /* ---- real generation: calls backend /api/generate, polls /api/jobs/{id} ---- */
  const startRealGenerate = useCallback(
    async (file: File) => {
      setAppState("generating");
      setProgress(0);
      setGenStep(GEN_STEPS[0][1]);
      setMsgs([{ role: "agent", text: "Uploading image to backend...", actions: ["upload()"] }]);

      try {
        const job = await startGenerate(file);
        setCurrentJobId(job.job_id);

        const finalJob = await pollJob(job.job_id, (j: Job) => {
          setProgress(j.progress);
          let step = GEN_STEPS[0][1];
          for (const st of GEN_STEPS) if (j.progress >= st[0]) step = st[1];
          setGenStep(step);
          if (j.status === "running") {
            setMsgs((prev: Msg[]) => {
              const last = prev[prev.length - 1];
              if (last && last.role === "agent" && last.actions?.[0]?.startsWith("progress")) {
                return prev;
              }
              return prev.concat([{ role: "agent", text: `${step} (${j.progress}%)`, actions: [`progress(${j.progress}%)`] }]);
            });
          }
        });

        if (finalJob.status === "error") {
          setAppState("error");
          setErrAssetName(finalJob.error || "Unknown error");
          setMsgs((prev: Msg[]) =>
            prev.concat([{ role: "agent", text: `Pipeline error: ${finalJob.error}`, actions: ["error()"] }]),
          );
          return;
        }

        const result = finalJob.result;
        if (!result) {
          setAppState("error");
          setErrAssetName("No result returned");
          return;
        }

        setCurrentModelId(result.model_id);
        const parts = result.parts || [];
        const single = parts.length <= 1;
        setSinglePart(single);
        setPartCount(parts.length);
        setAllParts(parts);

        // Enriched data
        setSourceImageUrl(result.source_image_url ? fileUrl(result.source_image_url) : "");
        setManualUrl(result.manual_url ? fileUrl(result.manual_url) : "");
        setAllCitations(result.citations || []);
        setObjectSummary(result.object_summary || "");
        setObjectType(result.object_type || "");
        setLikelyModel(result.likely_model || "");
        setExplodeFrames(result.explode_frames || []);
        setTurntableFrames(result.turntable_frames || []);
        setWarnings(result.warnings || []);

        // Load Snaplii actions from job result, or auto-create default
        const existingActions = result.snaplii_actions || [];
        if (existingActions.length > 0) {
          setSnapliiActions(existingActions);
        } else if (job.job_id) {
          try {
            const card = await createSnapliiAction(job.job_id, "manual_card");
            setSnapliiActions([card]);
          } catch {
            // Snaplii optional — silently skip on error
          }
        }

        const name = result.likely_model || result.object_type || (parts.length > 0 ? `${parts[0].label} assembly` : "Analyzed object");
        setModelName(name);
        setAppState("loaded");
        setExplode(0);
        setSelected(null);

        const v = viewerRef.current;
        if (v) {
          v.setSinglePart(single);
          v.reset();
        }

        const citationsCount = result.citations?.length || 0;
        const intro: Msg = {
          role: "agent",
          text: `Analysis complete — ${parts.length} parts identified via Gemini Vision + Google Search grounding (${citationsCount} citations). Ask me about any part, or tell me to explode, isolate, or focus.`,
          actions: [`analyze() - ${parts.length} parts, ${citationsCount} citations`],
        };
        setMsgs([intro]);

        if (!single && v) {
          setTimeout(() => {
            const partIds = parts.map((p) => p.part_id);
            if (partIds[1]) {
              v.selectPart(partIds[1]);
              selectById(partIds[1]);
            }
          }, 240);
        }
      } catch (e) {
        setAppState("error");
        setErrAssetName(String(e));
        setMsgs((prev: Msg[]) =>
          prev.concat([{ role: "agent", text: `Upload failed: ${e}`, actions: ["error()"] }]),
        );
      }
    },
    [selectById],
  );

  /* ---- assets / top-level actions ---- */
  const onNew = useCallback(() => {
    setAppState("empty");
    setCurrentJobId(null);
    setCurrentModelId(null);
    setAllParts([]);
    setAllCitations([]);
    setSourceImageUrl("");
    setManualUrl("");
    setObjectSummary("");
    setObjectType("");
    setLikelyModel("");
    setExplodeFrames([]);
    setTurntableFrames([]);
    setWarnings([]);
    setShowPartsPanel(false);
    setSnapliiActions([]);
    setMsgs([INTRO]);
  }, []);
  /* ---- real 2D pipeline: upload → /api/generate (mode=2d) → poll → play Kling video ---- */
  const runGenerate2D = useCallback(async (file: File) => {
    if (genTimer.current) clearInterval(genTimer.current);
    setAppState("generating");
    setProgress(0);
    setGenStep("Uploading photo…");
    setErrAssetName(file.name);
    try {
      const job = await apiGenerate(file, "2d");
      const final = await pollJob(job.job_id, (j) => {
        const p = j.progress ?? 0;
        setProgress(p);
        setGenStep(
          p < 45
            ? "Generating part image…"
            : p < 95
              ? "Synthesizing exploded frames…"
              : "Encoding sequence…",
        );
      });
      if (final.status === "error" || !final.result || final.result.kind !== "2d") {
        setErrAssetName(file.name);
        setAppState("error");
        return;
      }
      const r = final.result;
      setTwoDVideoSrc(fileUrl(r.video_url));
      setModelName(file.name.replace(/\.[^.]+$/, ""));
      setActiveAssetId("");
      setSinglePart(false);
      setExplode(0);
      setSelected(null);
      setAppState("loaded");
      setMsgs([
        {
          role: "agent",
          text: `Generated an exploded-view clip from ${file.name}. Drag the FRAME slider — 0% assembled, 100% exploded.`,
          actions: [`kling · ${r.frame_count ?? FRAMES_2D} frames`],
        },
      ]);
    } catch {
      setErrAssetName(file.name);
      setAppState("error");
    }
  }, []);

  const onFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files[0]) {
        startRealGenerate(e.target.files[0]);
      }
    },
    [startRealGenerate],
  );

  const switchMode = useCallback((m: Mode) => {
    setMode(m);
    setShowPartsPanel(false);
  }, []);

  /* ---- derived ---- */
  const is2d = mode === "2d";
  const isManual = mode === "manual";
  const is3d = mode === "3d";
  const explodePct = Math.round((explode || 0) * 100);
  const explodeLabel = !is2d && singlePart ? "N/A" : explodePct + "%";
  const sliderDisabled = appState !== "loaded" || (!is2d && singlePart) || isManual;

  const partCountLabel = singlePart ? "1 PART" : partCount + " PARTS";

  const suggestions = is2d
    ? [
        { label: "Run it apart", text: "Run it apart" },
        { label: "Back to assembled", text: "Back to assembled" },
        { label: "Explain the sequence", text: "Explain the sequence" },
      ]
    : [
        { label: "Show how it comes apart", text: "Show me how this comes apart" },
        { label: "What parts are visible?", text: "What parts can you identify?" },
        { label: "Which parts wear fastest?", text: "Which parts wear the fastest?" },
        { label: "Reset", text: "Reset the view" },
      ];

  // Use real frames for 2D if available, otherwise fallback to procedural
  const hasRealFrames = explodeFrames.length > 0 || turntableFrames.length > 0;

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
            aria-selected={is3d}
            className={`pl-seg-btn${is3d ? " active" : ""}`}
            onClick={() => switchMode("3d")}
          >
            <span className="dot" /> 3D
          </button>
          <button
            role="tab"
            aria-selected={is2d}
            className={`pl-seg-btn${is2d ? " active" : ""}`}
            onClick={() => switchMode("2d")}
          >
            <span className="dot" /> 2D
          </button>
          <button
            role="tab"
            aria-selected={isManual}
            className={`pl-seg-btn${isManual ? " active" : ""}`}
            onClick={() => switchMode("manual")}
            disabled={appState !== "loaded"}
          >
            <span className="dot" /> Manual
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

        <div className="pl-explode">
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
        </div>
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
            <span className="sub">{is2d ? "scrubs sequence" : isManual ? "viewing manual" : "acts on model"}</span>
          </div>

          {/* Source image thumbnail */}
          {sourceImageUrl && appState === "loaded" && (
            <div className="pl-source-img">
              <img src={sourceImageUrl} alt="Uploaded product" style={{ width: "100%", borderRadius: "6px", display: "block" }} />
              <div className="pl-source-label">SOURCE IMAGE</div>
            </div>
          )}

          {/* Object summary */}
          {appState === "loaded" && (objectSummary || likelyModel) && (
            <div className="pl-obj-summary">
              {likelyModel && <div className="pl-obj-model">{likelyModel}</div>}
              {objectType && <div className="pl-obj-type">Type: {objectType}</div>}
              {objectSummary && <div className="pl-obj-desc">{objectSummary}</div>}
            </div>
          )}

          {/* Parts breakdown toggle */}
          {appState === "loaded" && allParts.length > 0 && (
            <button className="pl-parts-toggle" onClick={() => setShowPartsPanel((s) => !s)}>
              {showPartsPanel ? "Hide" : "Show"} Parts Breakdown ({allParts.length})
            </button>
          )}

          {/* Parts breakdown panel */}
          {showPartsPanel && allParts.length > 0 && (
            <div className="pl-parts-panel">
              {allParts.map((p, i) => (
                <div className="pl-part-card" key={i}>
                  <div className="pl-part-id">{p.part_id}</div>
                  <div className="pl-part-label">{p.label}</div>
                  {p.description && <div className="pl-part-desc">{p.description}</div>}
                  {p.confidence !== undefined && (
                    <div className="pl-part-conf">
                      Confidence: {Math.round(p.confidence * 100)}% — {p.source_status || "vision_inferred"}
                    </div>
                  )}
                  {p.sources && p.sources.length > 0 && (
                    <div className="pl-part-sources">
                      {p.sources.map((s, j) => (
                        <a key={j} href={s.url} target="_blank" rel="noopener" className="pl-part-source">
                          {s.title}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Snaplii action cards */}
          {appState === "loaded" && snapliiActions.length > 0 && (
            <div className="pl-snaplii">
              <div className="pl-snaplii-head">NEXT STEPS WITH SNAPLII</div>
              {snapliiActions.map((action) => (
                <div className="pl-snaplii-card" key={action.id}>
                  <div className="pl-snaplii-info">
                    <div className="pl-snaplii-label">
                      {action.label}
                      {action.mock && <span className="pl-snaplii-mock">MOCK</span>}
                    </div>
                    <div className="pl-snaplii-status">Status: {action.status}</div>
                  </div>
                  <button
                    className="pl-snaplii-btn"
                    onClick={async () => {
                      try {
                        const a = await getSnapliiAction(currentJobId || "", action.id);
                        if (a.url) {
                          window.open(a.url, "_blank");
                        } else {
                          alert(`Snaplii action (mock mode)\nID: ${a.id}\nStatus: ${a.status}`);
                        }
                      } catch (e) {
                        alert(`Error: ${e}`);
                      }
                    }}
                  >
                    Open
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Citations */}
          {appState === "loaded" && allCitations.length > 0 && (
            <div className="pl-citations">
              <div className="pl-citations-head">SOURCES ({allCitations.length})</div>
              {allCitations.map((c, i) => (
                <a key={i} href={c.url} target="_blank" rel="noopener" className="pl-citation">
                  <span className="pl-citation-title">{c.title}</span>
                  {c.used_for && <span className="pl-citation-used">{c.used_for}</span>}
                </a>
              ))}
            </div>
          )}

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
                      {m.citations && m.citations.length > 0 && (
                        <div className="pl-msg-citations">
                          <small>Sources:</small>
                          {m.citations.map((c, j) => (
                            <a key={j} href={c.url} target="_blank" rel="noopener" className="pl-msg-citation">
                              {c.title}
                            </a>
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
                  onClick={() => handleUserText(s.text)}
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
                    ? "Scrub the sequence, or ask me to run it apart..."
                    : isManual
                    ? "Ask about the manual..."
                    : "Ask about a part, or tell me to isolate / focus / explode..."
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

        {/* CENTER: STAGE (hosts 3D, 2D, or Manual) */}
        <main className="pl-stage">
          <div className="pl-grid" />
          <div className="pl-glow" />
          <div className="pl-vignette" />
          <div className="pl-crosshair">
            <div className="v" />
            <div className="h" />
          </div>

          {/* 3D content - kept mounted so the WebGL context survives tab switches */}
          <div style={{ position: "absolute", inset: 0, display: is3d ? undefined : "none" }}>
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
            {hasRealFrames ? (
              <div className="pl-2d-real">
                {(() => {
                  const frames = explodeFrames.length > 0 ? explodeFrames : turntableFrames;
                  const idx = Math.min(Math.floor(explode * frames.length), frames.length - 1);
                  const currentFrame = frames[idx];
                  return (
                    <>
                      <img
                        src={currentFrame.startsWith("http") ? currentFrame : fileUrl(currentFrame)}
                        alt={`Frame ${idx + 1}`}
                        style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: "8px" }}
                      />
                      <div className="pl-2d-frame-info">
                        Frame {idx + 1} / {frames.length}
                        {explodeFrames.length > 0 ? " (Exploded)" : " (Turntable)"}
                      </div>
                    </>
                  );
                })()}
              </div>
            ) : (
              <TwoDStage factor={explode} active={is2d} frameCount={FRAMES_2D} />
            )}
          </div>

          {/* Manual content - iframe with backend HTML manual */}
          <div style={{ position: "absolute", inset: 0, display: isManual ? undefined : "none" }}>
            {manualUrl ? (
              <iframe
                src={manualUrl}
                title="Visual Manual"
                style={{ width: "100%", height: "100%", border: "none", borderRadius: "8px", background: "#0f1117" }}
              />
            ) : (
              <div className="pl-overlay">
                <div className="pl-gen-wrap">
                  <div className="pl-gen-label">NO MANUAL</div>
                  <div className="pl-gen-step">Manual not available. Upload an image first.</div>
                </div>
              </div>
            )}
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
                      ? "Gemini Vision + Google Search grounding"
                      : isManual
                      ? "Upload to generate a visual manual"
                      : "Gemini Vision analyzes & identifies parts"}
                  </div>
                </label>
              </div>
            </div>
          )}

          {appState === "generating" && (
            <div className="pl-overlay gen">
              <div className="pl-scan" />
              <div className="pl-gen-wrap">
                <div className="pl-gen-label">
                  {is2d ? "SYNTHESIZING" : isManual ? "BUILDING" : "ANALYZING"}
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
                <div className="pl-err-title">Analysis failed</div>
                <div className="pl-err-code">ERR_PIPELINE</div>
                <div className="pl-err-detail">
                  {errAssetName}
                </div>
                <div className="pl-cta-row">
                  <button className="pl-cta" onClick={onNew}>
                    Try again
                  </button>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
