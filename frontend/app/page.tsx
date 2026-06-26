"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ParallaxViewer, PartMeta } from "@/lib/parallax-viewer";

/* ---- types ---- */
type AppState = "loaded" | "empty" | "generating" | "error";
type AssetType = "multi" | "single" | "error";
type Selected = Pick<PartMeta, "id" | "name" | "note">;

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

const INTRO: Msg = {
  role: "agent",
  text:
    "Model ready — Single-Cylinder Assembly. I resolved 8 separable parts from your photo. Pick a part on the stage, drag the explode slider, or ask me to isolate, focus, or flag wear surfaces.",
  actions: ["reconstruct() · 8 parts"],
};

const GEN_STEPS: [number, string][] = [
  [0, "Segmenting source image…"],
  [22, "Estimating depth & silhouette…"],
  [44, "Reconstructing part geometry…"],
  [66, "Resolving part boundaries…"],
  [84, "Assigning material & axes…"],
];

export default function Home() {
  const [appState, setAppState] = useState<AppState>("loaded");
  const [modelName, setModelName] = useState("Single-Cylinder Assembly");
  const [explode, setExplode] = useState(0);
  const [singlePart, setSinglePart] = useState(false);
  const [partCount, setPartCount] = useState(8);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [progress, setProgress] = useState(0);
  const [genStep, setGenStep] = useState("");
  const [activeAssetId, setActiveAssetId] = useState("ASSET-01");
  const [errAssetName, setErrAssetName] = useState("");
  const [selected, setSelected] = useState<Selected | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([INTRO]);

  const viewerRef = useRef<ParallaxViewer | null>(null);
  const stageElRef = useRef<HTMLDivElement | null>(null);
  const logElRef = useRef<HTMLDivElement | null>(null);
  const genTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const thinkTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progAccum = useRef(0);
  const genAssetRef = useRef<Asset | null>(null);
  const singlePartRef = useRef(false);

  singlePartRef.current = singlePart;

  /* ---- viewer lifecycle ---- */
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
      // preselect the piston, like the prototype's "already inspected" state
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
      viewerRef.current = null;
    };
  }, []);

  // keep the message log pinned to the newest message
  useEffect(() => {
    if (logElRef.current) logElRef.current.scrollTop = logElRef.current.scrollHeight;
  }, [msgs, thinking]);

  // clean up timers on unmount
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

  /* ---- scripted agent (acts on the live viewer) ---- */
  const handleUserText = useCallback(
    (text: string) => {
      setMsgs((prev) => prev.concat([{ role: "user", text }]));
      setDraft("");
      setThinking(true);
      if (thinkTimer.current) clearTimeout(thinkTimer.current);

      thinkTimer.current = setTimeout(() => {
        const v = viewerRef.current;
        const intents: {
          match: RegExp;
          actions: string[];
          text: string;
          run: () => void;
        }[] = [
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

        if (
          singlePartRef.current &&
          intent &&
          /explode/i.test(intent.actions.join(" "))
        ) {
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
    [selectById],
  );

  const send = useCallback(() => {
    const t = draft.trim();
    if (!t || appState !== "loaded") return;
    handleUserText(t);
  }, [draft, appState, handleUserText]);

  const onKey = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    },
    [send],
  );

  /* ---- slider ---- */
  const onSlider = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value);
    setExplode(v);
    viewerRef.current?.setExplode(v);
  }, []);

  /* ---- selected-part card ---- */
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

  /* ---- generation simulation ---- */
  const finishGenerate = useCallback((a: Asset) => {
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
  }, [selectById]);

  const startGenerate = useCallback(
    (a: Asset) => {
      if (genTimer.current) clearInterval(genTimer.current);
      progAccum.current = 0;
      genAssetRef.current = a;
      setAppState("generating");
      setProgress(0);
      setGenStep("Segmenting source image…");

      genTimer.current = setInterval(() => {
        progAccum.current += 3 + Math.random() * 4;
        const p = progAccum.current;

        if (a.type === "error" && p >= 70) {
          if (genTimer.current) clearInterval(genTimer.current);
          setAppState("error");
          setErrAssetName(a.name);
          setProgress(70);
          return;
        }
        if (p >= 100) {
          if (genTimer.current) clearInterval(genTimer.current);
          setProgress(100);
          finishGenerate(a);
          return;
        }
        let step = GEN_STEPS[0][1];
        for (const st of GEN_STEPS) if (p >= st[0]) step = st[1];
        setProgress(p);
        setGenStep(step);
      }, 95);
    },
    [finishGenerate],
  );

  /* ---- assets / top-level actions ---- */
  const toggleAssets = useCallback(() => setDrawerOpen((o) => !o), []);
  const onNew = useCallback(() => {
    setAppState("empty");
    setDrawerOpen(false);
  }, []);
  const onFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files[0]) startGenerate(ASSETS[0]);
    },
    [startGenerate],
  );
  const useSample = useCallback(() => startGenerate(ASSETS[0]), [startGenerate]);
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

  /* ---- derived ---- */
  const explodePct = Math.round((explode || 0) * 100);
  const explodeLabel = singlePart ? "N/A" : explodePct + "%";
  const sliderDisabled = appState !== "loaded" || singlePart;
  const partCountLabel = singlePart ? "1 PART" : partCount + " PARTS";

  const suggestions = [
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
        <button className="pl-btn outline" onClick={onNew}>
          <span className="plus">+</span> Upload / New
        </button>
        <div className="pl-model-tag">
          <span className="k">MODEL</span>
          <span className="v">{modelName}</span>
        </div>

        <div className="pl-spacer" />

        <div className="pl-explode">
          <span className="lbl">EXPLODE</span>
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
            <span className="sub">acts on model</span>
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
                placeholder="Ask about a part, or tell me to isolate / focus / explode…"
              />
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

        {/* CENTER: STAGE */}
        <main className="pl-stage">
          <div className="pl-grid" />
          <div className="pl-glow" />
          <div className="pl-vignette" />
          <div className="pl-crosshair">
            <div className="v" />
            <div className="h" />
          </div>

          <div className="pl-mount" ref={stageElRef} />

          {/* top-left HUD */}
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

          {/* selected part card */}
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

          {/* ===== STATE OVERLAYS ===== */}
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
                    Parallax segments &amp; reconstructs the parts
                  </div>
                </label>
                <div className="pl-cta-row">
                  <button className="pl-cta" onClick={useSample}>
                    Use sample assembly
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
                <div className="pl-gen-label">RECONSTRUCTING</div>
                <div className="pl-gen-num">
                  <span className="n">{Math.round(progress)}</span>
                  <span className="pct">%</span>
                </div>
                <div className="pl-gen-bar">
                  <div
                    className="fill"
                    style={{ width: Math.round(progress) + "%" }}
                  />
                </div>
                <div className="pl-gen-step">{genStep}</div>
              </div>
            </div>
          )}

          {appState === "error" && (
            <div className="pl-overlay err">
              <div className="pl-err-wrap">
                <div className="pl-err-icon">!</div>
                <div className="pl-err-title">Reconstruction failed</div>
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
                      style={{
                        borderColor: active ? ACCENT : "rgba(255,255,255,.22)",
                      }}
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
