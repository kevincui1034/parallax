# Claude Design prompt — Parallax viewer UI

> Paste the block below into Claude Design. It's self-contained. Everything after
> this line is the prompt.

---

Design a single-screen web app called **Parallax** — a tool that turns a product
photo into an interactive **exploded-parts 3D diagram** you can inspect and query.
The user uploads a photo, the app generates a part-separated 3D model, and an AI
agent answers questions by *acting* on the model (exploding it, highlighting and
isolating parts, focusing the camera). It should feel like a serious engineering
inspection instrument, not a consumer toy.

## Visual direction — technical / CAD-dark

- Near-black 3D stage so the rendered model is the brightest thing on screen. The
  surrounding chrome (navbar, panels) is dark but a step lighter than the stage,
  so the hierarchy is stage → panels → navbar.
- Precise, instrument-like. Thin 1px hairline borders, generous but disciplined
  spacing, a faint technical grid or dotted texture in the stage background.
- Type: a clean grotesk for UI text (e.g. Inter / Geist) paired with a monospace
  for technical metadata — part IDs, coordinates, dimensions, progress percentages.
  The mono is what sells the "engineering tool" read.
- A single restrained accent — electric cyan — used only for the active/selected
  state, the highlighted part, focus rings, and the primary action. Everything
  else is neutral grays on near-black. No second accent.
- Ensure body text meets AA contrast on the dark surfaces (no dim gray on dark).
- Motion is functional and quick: parts ease apart on explode (ease-out, no
  bounce), panel slides in smoothly, selection states crossfade. Respect
  prefers-reduced-motion.

## Layout

```
┌──────────────────────────── top navbar ─────────────────────────────────┐
│  ◆ Parallax   ·   [ + New / Upload ]   ·   model name   ·  explode ▭▭▭○──  ·  ⧉ Assets │
├───────────────────┬──────────────────────────────────────────────────────┤
│  CHAT + COMPOSER  │                                                        │
│  (left column,    │              3D MODEL VIEWER                           │
│   ~360px, fixed)  │              (center, fills remaining space)           │
│                   │                                                        │
│  message log ↑    │   model assembled by default, on near-black stage      │
│  (agent + user)   │   orbit / zoom; click a part to inspect                │
│                   │                                                        │
│  ─────────────    │                                                        │
│  composer:        │                                                        │
│  textarea + send  │                                                        │
└───────────────────┴──────────────────────────────────────────────────────┘

  ASSETS PANEL  ◀ pops out and OVERLAYS from the left edge (toggled in navbar).
  It floats above the chat as a dark glass/solid drawer; it does NOT push the
  layout. Shows a grid of generated assets (source photo + each generated model);
  clicking one loads it into the center viewer. A close affordance / click-outside
  dismisses it.
```

### Top navbar
App mark + name on the left. Primary action: **Upload / New** (start from a photo).
Center: current model name/label. Right side: the **explode slider** (0 → 1,
continuous) and a toggle to open the **Assets** panel. Slim, dark, mono labels.

### Left column — chat + composer (persistent)
The agent conversation. Scrolling message log of user questions and agent replies;
agent replies may be accompanied by small inline chips noting the action it took
("exploded to 70%", "isolated: housing"). Pinned composer at the bottom: a
multi-line textarea with a send button (Enter to send, Shift+Enter for newline).
Asking a question triggers the model in the center to move.

### Center — 3D model viewer (main stage)
The hero. A dark 3D canvas showing the model assembled. Orbit/zoom controls.
Clicking a part selects + inspects it (show its label and a small mono readout of
its id / dimensions in a corner overlay). The explode slider spreads the parts
radially from the model's center. Highlighted/isolated/focused states are driven
both by clicking and by the agent.

### Assets panel — left popout overlay
Hidden by default; opens from the navbar toggle. Slides in from the left edge as a
dark overlay drawer on top of the chat (does not reflow layout). A compact grid of
generated assets — the uploaded source image thumbnail and each generated model,
each with a small mono caption (e.g. part count, timestamp). Selecting one loads
it into the viewer and closes the panel.

## States to design (all of them)

1. **Empty / first run** — no model yet. The stage shows an upload affordance:
   drop a product photo or browse. Clear, inviting, instrument-like.
2. **Generating** — generation takes ~30s+. Show progress (0–100%, mono) in the
   stage area with a tasteful technical loading treatment. Chat is usable; viewer
   is pending.
3. **Loaded** — model assembled, explode slider at 0, chat ready. The normal state.
4. **One-part fallback** — sometimes the model comes back as a single fused mesh
   (one part). The explode slider then does nothing; inspect still works. The UI
   must look intentional with one part, not broken.
5. **Error** — generation failed. Show a readable message in the stage and a retry
   action.

## Agent action vocabulary (what makes the model move — design feedback for each)
- **explode(factor 0–1)** — sets the explode slider; parts spread radially.
- **highlight(part)** — one part emphasized with the accent (outline/glow).
- **isolate(parts)** — show only the named parts, dim/hide the rest.
- **focus(part)** — camera moves to frame one part.
- **reset** — reassembled, all parts visible, camera home.

## Don'ts
- No light/cream theme. No glassmorphism everywhere (one purposeful dark glass
  drawer is fine). No gradient text. No card-grid filler. The 3D stage is the
  star — keep chrome quiet so the model dominates.
