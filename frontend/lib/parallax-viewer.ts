// Parallax 3D viewer engine — builds a stand-in engine piston/valve assembly
// from THREE primitives and exposes the contract action vocabulary
// (explode / highlight / isolate / focus / reset) for the agent to drive.
//
// Ported from the Claude Design prototype (parallax-viewer.js) to the npm
// `three` package. The procedural assembly is the fixture stand-in; swapping
// _buildAssembly for a GLTFLoader over ModelResult.parts wires the real
// PartCrafter output without touching the action API below.
import * as THREE from "three";

export interface PartMeta {
  id: string;
  name: string;
  note: string;
  spread: [number, number, number];
}

export interface ViewerOptions {
  accent?: string;
  onPick?: (meta: PartMeta | null) => void;
}

interface PartLabel {
  sprite: THREE.Sprite;
  homeOffset: THREE.Vector3;
}

interface FrameSeqState {
  textures: THREE.Texture[];
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
  baseGeometry: THREE.BufferGeometry;
  depthData: Float32Array | null;
  depthCache: Map<THREE.Texture, Float32Array>;
  count: number;
  loaded: boolean;
  partLabels: PartLabel[];
}

interface PartUserData {
  partId: string;
  home: THREE.Vector3;
  spread: THREE.Vector3;
  emiGoal: number;
  emi: number;
  opGoal: number;
  op: number;
  meta: PartMeta;
}

// A helical curve used to build the valve spring as a real swept tube.
class HelixCurve extends THREE.Curve<THREE.Vector3> {
  constructor(
    private radius: number,
    private height: number,
    private turns: number,
  ) {
    super();
  }
  getPoint(t: number, optionalTarget = new THREE.Vector3()): THREE.Vector3 {
    const a = this.turns * Math.PI * 2 * t;
    return optionalTarget.set(
      Math.cos(a) * this.radius,
      t * this.height - this.height / 2,
      Math.sin(a) * this.radius,
    );
  }
}

// Equirect studio environment so metals have something to reflect on a dark stage.
function makeEnvTexture(): THREE.Texture {
  const cv = document.createElement("canvas");
  cv.width = 512;
  cv.height = 256;
  const x = cv.getContext("2d")!;
  x.fillStyle = "#0b0f13";
  x.fillRect(0, 0, 512, 256);
  let g = x.createRadialGradient(150, 60, 10, 150, 60, 170);
  g.addColorStop(0, "rgba(220,235,245,0.95)");
  g.addColorStop(1, "rgba(11,15,19,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 512, 256);
  g = x.createRadialGradient(400, 120, 10, 400, 120, 150);
  g.addColorStop(0, "rgba(90,120,140,0.55)");
  g.addColorStop(1, "rgba(11,15,19,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 512, 256);
  const tex = new THREE.CanvasTexture(cv);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  return tex;
}

function steel(
  hex: number,
  opts: { metalness?: number; roughness?: number } = {},
): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: hex,
    metalness: opts.metalness != null ? opts.metalness : 0.92,
    roughness: opts.roughness != null ? opts.roughness : 0.34,
    emissive: new THREE.Color(0x000000),
    emissiveIntensity: 0,
    transparent: true,
    opacity: 1,
  });
}

export class ParallaxViewer {
  private container: HTMLElement;
  private accent: THREE.Color;
  private onPick: (meta: PartMeta | null) => void;
  private reduced: boolean;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private assembly: THREE.Group;
  private parts: THREE.Group[] = [];
  private ray: THREE.Raycaster;
  private frameSeq: FrameSeqState | null = null;

  private sph = { radius: 26, theta: 0.7, phi: 1.08 };
  private sphTarget = { radius: 26, theta: 0.7, phi: 1.08 };
  private target = new THREE.Vector3(0, 0.3, 0);
  private targetGoal = new THREE.Vector3(0, 0.3, 0);

  private explode = 0;
  private explodeGoal = 0;
  private singlePart = false;
  private autoOrbit = false;
  private _dragging = false;
  private _running = false;

  private _onResize: () => void;
  private _ro?: ResizeObserver;

  constructor(container: HTMLElement, opts: ViewerOptions = {}) {
    this.container = container;
    this.accent = new THREE.Color(opts.accent || "#3ad8ff");
    this.onPick = opts.onPick || (() => {});
    this.reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const w = container.clientWidth || 800;
    const h = container.clientHeight || 600;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h);
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";
    this.renderer = renderer;

    const scene = new THREE.Scene();
    this.scene = scene;
    scene.environment = makeEnvTexture();

    const camera = new THREE.PerspectiveCamera(38, w / h, 0.1, 500);
    this.camera = camera;

    const hemi = new THREE.HemisphereLight(0x2a3b46, 0x05070a, 0.5);
    scene.add(hemi);
    const key = new THREE.DirectionalLight(0xeaf4ff, 1.25);
    key.position.set(8, 14, 10);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x8fb6c8, 0.7);
    rim.position.set(-10, 6, -8);
    scene.add(rim);
    const fill = new THREE.DirectionalLight(0xffffff, 0.25);
    fill.position.set(0, -8, 6);
    scene.add(fill);

    this.assembly = new THREE.Group();
    scene.add(this.assembly);

    this._buildAssembly();

    this.ray = new THREE.Raycaster();
    this._bindInput();
    this._onResize = this.onResize.bind(this);
    window.addEventListener("resize", this._onResize);
    if (window.ResizeObserver) {
      this._ro = new ResizeObserver(this._onResize);
      this._ro.observe(container);
    }

    this._running = true;
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  private mat(
    hex: number,
    opts: { metalness?: number; roughness?: number } = {},
  ): THREE.MeshStandardMaterial {
    const m = steel(hex, opts);
    m.emissive = this.accent.clone();
    m.emissiveIntensity = 0;
    return m;
  }

  private registerPart(
    group: THREE.Group,
    def: PartMeta,
  ): void {
    const ud = group.userData as unknown as PartUserData;
    ud.partId = def.id;
    ud.home = group.position.clone();
    ud.spread = new THREE.Vector3().fromArray(def.spread);
    ud.emiGoal = 0;
    ud.emi = 0;
    ud.opGoal = 1;
    ud.op = 1;
    ud.meta = def;
    group.traverse((o) => {
      o.userData.partId = def.id;
    });
    this.parts.push(group);
    this.assembly.add(group);
  }

  private _buildAssembly(): void {
    // P-01 Cylinder Sleeve (semi-transparent, open ended)
    {
      const g = new THREE.Group();
      const sleeve = new THREE.Mesh(
        new THREE.CylinderGeometry(2.15, 2.15, 4.4, 48, 1, true),
        this.mat(0x9aa4ad, { roughness: 0.22, metalness: 0.6 }),
      );
      sleeve.material.transparent = true;
      sleeve.material.opacity = 0.32;
      sleeve.material.side = THREE.DoubleSide;
      g.add(sleeve);
      g.position.set(0, 1.0, 0);
      this.registerPart(g, {
        id: "P-01",
        name: "Cylinder Sleeve",
        spread: [5.5, 0, 0],
        note: "Hardened liner. Guides the piston and seals the combustion chamber.",
      });
    }

    // P-02 Piston
    {
      const g = new THREE.Group();
      const body = new THREE.Mesh(
        new THREE.CylinderGeometry(1.78, 1.78, 2.2, 48),
        this.mat(0xc2cad2, { roughness: 0.3 }),
      );
      const crown = new THREE.Mesh(
        new THREE.CylinderGeometry(1.78, 1.78, 0.18, 48),
        this.mat(0x8b939b, { roughness: 0.5 }),
      );
      crown.position.y = 1.1;
      g.add(body, crown);
      const boss = new THREE.Mesh(
        new THREE.CylinderGeometry(0.55, 0.55, 3.4, 24),
        this.mat(0x9aa4ad),
      );
      boss.rotation.z = Math.PI / 2;
      boss.position.y = -0.2;
      g.add(boss);
      g.position.set(0, 1.0, 0);
      this.registerPart(g, {
        id: "P-02",
        name: "Piston",
        spread: [0, 1.4, 0],
        note: "Transfers combustion pressure to the rod. Aluminum alloy, skirt-guided.",
      });
    }

    // P-03 Compression Ring
    {
      const g = new THREE.Group();
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(1.82, 0.12, 16, 64),
        this.mat(0x5a626b, { roughness: 0.5, metalness: 0.8 }),
      );
      ring.rotation.x = Math.PI / 2;
      g.add(ring);
      g.position.set(0, 1.85, 0);
      this.registerPart(g, {
        id: "P-03",
        name: "Compression Ring",
        spread: [0, 3.2, 0],
        note: "Seals combustion gases against the bore. A primary wear surface.",
      });
    }

    // P-04 Wrist Pin
    {
      const g = new THREE.Group();
      const pin = new THREE.Mesh(
        new THREE.CylinderGeometry(0.42, 0.42, 3.6, 32),
        this.mat(0xd0d7de, { roughness: 0.18, metalness: 0.95 }),
      );
      pin.rotation.z = Math.PI / 2;
      g.add(pin);
      g.position.set(0, 0.8, 0);
      this.registerPart(g, {
        id: "P-04",
        name: "Wrist Pin",
        spread: [0, 0, 5.5],
        note: "Floating gudgeon pin linking piston to connecting rod.",
      });
    }

    // P-05 Connecting Rod
    {
      const g = new THREE.Group();
      const small = new THREE.Mesh(
        new THREE.TorusGeometry(0.6, 0.28, 16, 32),
        this.mat(0x868e96),
      );
      small.rotation.x = Math.PI / 2;
      small.position.y = 1.6;
      const big = new THREE.Mesh(
        new THREE.TorusGeometry(1.0, 0.34, 16, 40),
        this.mat(0x868e96),
      );
      big.rotation.x = Math.PI / 2;
      big.position.y = -1.7;
      const shaft = new THREE.Mesh(
        new THREE.BoxGeometry(0.5, 3.4, 0.85),
        this.mat(0x7b838b),
      );
      g.add(small, big, shaft);
      g.position.set(0, -1.0, 0);
      this.registerPart(g, {
        id: "P-05",
        name: "Connecting Rod",
        spread: [0, -3.6, 0],
        note: "Converts the piston’s linear motion into rotation at the crank.",
      });
    }

    // P-06 Crank Journal
    {
      const g = new THREE.Group();
      const pin = new THREE.Mesh(
        new THREE.CylinderGeometry(0.95, 0.95, 3.0, 32),
        this.mat(0xb6bec6, { roughness: 0.22, metalness: 0.95 }),
      );
      pin.rotation.z = Math.PI / 2;
      const wA = new THREE.Mesh(
        new THREE.BoxGeometry(0.7, 2.6, 1.6),
        this.mat(0x6f777f),
      );
      wA.position.x = -1.3;
      wA.position.y = -0.4;
      const wB = wA.clone();
      wB.position.x = 1.3;
      g.add(pin, wA, wB);
      g.position.set(0, -3.7, 0);
      this.registerPart(g, {
        id: "P-06",
        name: "Crank Journal",
        spread: [0, -6.4, 0],
        note: "Offset bearing surface that turns rod motion into shaft rotation.",
      });
    }

    // P-07 Intake Valve
    {
      const g = new THREE.Group();
      const stem = new THREE.Mesh(
        new THREE.CylinderGeometry(0.16, 0.16, 3.2, 24),
        this.mat(0xd0d7de, { roughness: 0.2, metalness: 0.95 }),
      );
      stem.position.y = 1.6;
      const head = new THREE.Mesh(
        new THREE.CylinderGeometry(0.95, 0.7, 0.5, 40),
        this.mat(0xaeb6be, { roughness: 0.3 }),
      );
      head.position.y = -0.1;
      g.add(stem, head);
      g.position.set(0, 3.4, 0);
      this.registerPart(g, {
        id: "P-07",
        name: "Intake Valve",
        spread: [0, 4.6, 0],
        note: "Admits the air–fuel charge. Seats under spring load.",
      });
    }

    // P-08 Valve Spring
    {
      const g = new THREE.Group();
      const curve = new HelixCurve(0.62, 2.4, 6);
      const spring = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 220, 0.1, 10, false),
        this.mat(0x7f878f, { roughness: 0.4, metalness: 0.85 }),
      );
      g.add(spring);
      g.position.set(0, 5.2, 0);
      this.registerPart(g, {
        id: "P-08",
        name: "Valve Spring",
        spread: [0, 6.6, 0],
        note: "Returns the valve to its seat and controls float at speed.",
      });
    }
  }

  partList(): Pick<PartMeta, "id" | "name" | "note">[] {
    return this.parts.map((p) => {
      const meta = (p.userData as unknown as PartUserData).meta;
      return { id: meta.id, name: meta.name, note: meta.note };
    });
  }

  /**
   * Update the metadata (name, note) of existing parts to match real analysis.
   * Maps external part labels onto the procedural geometry by index.
   */
  updatePartMetadata(externalParts: { label: string; description: string }[]): void {
    const count = Math.min(externalParts.length, this.parts.length);
    for (let i = 0; i < count; i++) {
      const ud = this.parts[i].userData as unknown as PartUserData;
      ud.meta.name = externalParts[i].label;
      ud.meta.note = externalParts[i].description;
    }
  }

  /**
   * Load real generated Kling explode frames and display them on a
   * depth-displaced mesh. The explode slider scrubs through frames and
   * increases depth displacement, creating a 3D deconstruction view.
   * Part labels float in 3D space as parts deconstruct.
   */
  setFrameSequence(
    frameUrls: string[],
    sourceImageUrl?: string,
    parts?: { label: string; description: string }[],
  ): void {
    if (frameUrls.length === 0) return;
    const loader = new THREE.TextureLoader();

    const allUrls = sourceImageUrl
      ? [sourceImageUrl, ...frameUrls]
      : frameUrls;

    // Build or reuse the displaced mesh
    if (!this.frameSeq) {
      const geo = new THREE.PlaneGeometry(12, 12, 128, 128);
      const mat = new THREE.MeshStandardMaterial({
        roughness: 0.65,
        metalness: 0.15,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 1,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.visible = false;
      this.scene.add(mesh);
      this.frameSeq = {
        textures: [],
        mesh,
        mat,
        baseGeometry: geo.clone(),
        depthData: null,
        depthCache: new Map(),
        count: 0,
        loaded: false,
        partLabels: [],
      };
    }

    const fs = this.frameSeq;
    fs.count = allUrls.length;
    fs.loaded = false;

    // Dispose old labels
    fs.partLabels.forEach((p) => {
      this.scene.remove(p.sprite);
      p.sprite.material.map?.dispose();
      p.sprite.material.dispose();
    });
    fs.partLabels = [];

    const loadAll = async () => {
      for (let i = 0; i < allUrls.length; i++) {
        try {
          const tex = await loader.loadAsync(allUrls[i]);
          tex.colorSpace = THREE.SRGBColorSpace;
          fs.textures[i] = tex;
          if (i === 0) {
            const img = tex.image as HTMLImageElement;
            const aspect = img.width / img.height || 1;
            fs.mesh.geometry.dispose();
            const newGeo = new THREE.PlaneGeometry(12 * aspect, 12, 128, 128);
            fs.mesh.geometry = newGeo;
            fs.baseGeometry = newGeo.clone();
            fs.mat.map = tex;
            fs.mat.needsUpdate = true;
            fs.depthData = this._estimateDepth(tex);
            this._applyDepth(0);
          }
        } catch {
          // skip failed frames
        }
      }

      fs.loaded = true;

      // Create part labels
      if (parts && parts.length > 0) {
        this._createPartLabels(parts);
      }

      // Hide procedural parts
      this.parts.forEach((p) => (p.visible = false));
      fs.mesh.visible = true;

      // Camera setup
      this.targetGoal.set(0, 0, 0);
      this.target.set(0, 0, 0);
      this.sphTarget.radius = 16;
      this.sphTarget.theta = Math.PI / 2;
      this.sphTarget.phi = Math.PI / 2;
      this.sph.radius = 16;
      this.sph.theta = Math.PI / 2;
      this.sph.phi = Math.PI / 2;
    };

    loadAll();
  }

  /**
   * Create floating text sprites for each part, positioned around the mesh.
   * Labels fade in and move outward as the explode value increases.
   */
  private _createPartLabels(
    parts: { label: string; description: string }[],
  ): void {
    if (!this.frameSeq) return;
    const fs = this.frameSeq;

    parts.forEach((part, i) => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 80;
      const ctx = canvas.getContext("2d")!;

      // Background pill
      ctx.fillStyle = "rgba(8, 16, 24, 0.85)";
      ctx.roundRect(4, 4, 312, 72, 12);
      ctx.fill();

      // Accent border
      ctx.strokeStyle = "rgba(58, 216, 255, 0.6)";
      ctx.lineWidth = 2;
      ctx.roundRect(4, 4, 312, 72, 12);
      ctx.stroke();

      // Label text
      ctx.fillStyle = "#3ad8ff";
      ctx.font = "bold 22px sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(part.label, 16, 30);

      // Description (truncated)
      ctx.fillStyle = "rgba(255, 255, 255, 0.7)";
      ctx.font = "14px sans-serif";
      const desc = part.description.length > 45
        ? part.description.slice(0, 42) + "..."
        : part.description;
      ctx.fillText(desc, 16, 54);

      const texture = new THREE.CanvasTexture(canvas);
      texture.colorSpace = THREE.SRGBColorSpace;
      const material = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        opacity: 0,
        depthTest: false,
      });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(5, 1.25, 1);

      // Position around the mesh at different angles
      const angle = (i / parts.length) * Math.PI * 2 - Math.PI / 2;
      const radius = 9;
      const homeOffset = new THREE.Vector3(
        Math.cos(angle) * radius,
        Math.sin(angle) * radius * 0.6,
        2,
      );
      sprite.position.copy(homeOffset);
      this.scene.add(sprite);
      fs.partLabels.push({ sprite, homeOffset });
    });
  }

  /**
   * Estimate a depth map from texture luminance. Uses caching to avoid
   * recomputing for the same texture. Brighter pixels = closer (positive Z),
   * darker = farther. Center bias makes the subject pop forward.
   */
  private _estimateDepth(tex: THREE.Texture): Float32Array {
    if (!this.frameSeq) return new Float32Array(0);
    const cached = this.frameSeq.depthCache.get(tex);
    if (cached) return cached;

    const img = tex.image as HTMLImageElement;
    const w = 128;
    const h = 128;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, 0, 0, w, h);
    const pixels = ctx.getImageData(0, 0, w, h).data;

    const depth = new Float32Array(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const lum =
          (0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2]) /
          255;
        const cx = (x - w / 2) / (w / 2);
        const cy = (y - h / 2) / (h / 2);
        const centerDist = Math.sqrt(cx * cx + cy * cy);
        const centerBias = Math.max(0, 1 - centerDist * 0.7);
        // Edge detection: larger luminance gradient = more depth
        const gx = x > 0 && x < w - 1
          ? Math.abs(pixels[(y * w + x + 1) * 4] - pixels[(y * w + x - 1) * 4]) / 255
          : 0;
        const gy = y > 0 && y < h - 1
          ? Math.abs(pixels[((y + 1) * w + x) * 4] - pixels[((y - 1) * w + x) * 4]) / 255
          : 0;
        const edge = Math.min(1, (gx + gy) * 2);
        depth[y * w + x] = lum * 0.35 + centerBias * 0.45 + edge * 0.2;
      }
    }
    this.frameSeq.depthCache.set(tex, depth);
    return depth;
  }

  /**
   * Apply depth displacement to the mesh vertices.
   * @param explode 0 = flat, 1 = fully displaced (parts lifted in Z)
   */
  private _applyDepth(explode: number): void {
    if (!this.frameSeq || !this.frameSeq.depthData) return;
    const geo = this.frameSeq.mesh.geometry as THREE.BufferGeometry;
    const base = this.frameSeq.baseGeometry;
    const positions = geo.attributes.position as THREE.BufferAttribute;
    const basePos = base.attributes.position as THREE.BufferAttribute;
    const depth = this.frameSeq.depthData;
    const w = 128;
    const h = 128;
    const maxDepth = 3.5; // max Z displacement in world units

    for (let i = 0; i < positions.count; i++) {
      // Map vertex index to depth grid
      const ix = i % (w + 1);
      const iy = Math.floor(i / (w + 1));
      const dx = Math.min(Math.floor((ix / (w + 1)) * w), w - 1);
      const dy = Math.min(Math.floor((iy / (h + 1)) * h), h - 1);
      const d = depth[dy * w + dx] || 0;
      const z = (d - 0.5) * maxDepth * explode;
      positions.setZ(i, basePos.getZ(i) + z);
    }
    positions.needsUpdate = true;
    geo.computeVertexNormals();
  }

  /** True when real generated frames are active (no procedural geometry). */
  hasFrameSequence(): boolean {
    return !!(this.frameSeq && this.frameSeq.loaded);
  }

  /** Remove frame sequence and restore procedural geometry. */
  clearFrameSequence(): void {
    if (!this.frameSeq) return;
    this.scene.remove(this.frameSeq.mesh);
    this.frameSeq.textures.forEach((t) => t.dispose());
    this.frameSeq.partLabels.forEach((p) => {
      this.scene.remove(p.sprite);
      p.sprite.material.map?.dispose();
      p.sprite.material.dispose();
    });
    this.frameSeq.mat.dispose();
    this.frameSeq.mesh.geometry.dispose();
    this.frameSeq.baseGeometry.dispose();
    this.frameSeq.depthCache.clear();
    this.frameSeq = null;
    this.parts.forEach((p) => (p.visible = true));
  }

  private _bindInput(): void {
    const el = this.renderer.domElement;
    el.style.touchAction = "none";
    let dragging = false;
    let lx = 0;
    let ly = 0;
    let moved = 0;
    el.addEventListener("pointerdown", (e) => {
      dragging = true;
      this._dragging = true;
      lx = e.clientX;
      ly = e.clientY;
      moved = 0;
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - lx;
      const dy = e.clientY - ly;
      lx = e.clientX;
      ly = e.clientY;
      moved += Math.abs(dx) + Math.abs(dy);
      this.sphTarget.theta -= dx * 0.006;
      this.sphTarget.phi = Math.max(
        0.25,
        Math.min(Math.PI - 0.25, this.sphTarget.phi - dy * 0.006),
      );
      this.sph.theta = this.sphTarget.theta;
      this.sph.phi = this.sphTarget.phi;
    });
    el.addEventListener("pointerup", (e) => {
      dragging = false;
      this._dragging = false;
      if (moved < 6) this._pick(e);
    });
    el.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        this.sphTarget.radius = Math.max(
          10,
          Math.min(48, this.sphTarget.radius + e.deltaY * 0.02),
        );
      },
      { passive: false },
    );
  }

  private _pick(e: PointerEvent): void {
    // Skip picking when real frames are active — no 3D parts to pick
    if (this.hasFrameSequence()) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const m = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.ray.setFromCamera(m, this.camera);
    const hits = this.ray.intersectObjects(this.assembly.children, true);
    const hit = hits.find(
      (h) =>
        h.object.userData.partId &&
        (h.object as THREE.Mesh).material &&
        ((h.object as THREE.Mesh).material as THREE.Material & {
          opacity: number;
        }).opacity > 0.12,
    );
    if (hit) {
      const id = hit.object.userData.partId as string;
      this.selectPart(id);
      const meta = this.parts.find(
        (p) => (p.userData as unknown as PartUserData).partId === id,
      )!.userData.meta as PartMeta;
      this.onPick(meta);
    } else {
      this.clearHighlight();
      this.onPick(null);
    }
  }

  // ---- Agent actions ----
  setExplode(f: number): void {
    if (this.hasFrameSequence()) {
      f = Math.max(0, Math.min(1, f));
      this.explodeGoal = f;
      // Pull camera back slightly as parts deconstruct
      this.sphTarget.radius = 16 + f * 6;
      return;
    }
    if (this.singlePart) {
      this.explodeGoal = 0;
      return;
    }
    f = Math.max(0, Math.min(1, f));
    this.explodeGoal = f;
    this.sphTarget.radius = 24 + f * 10;
    this.targetGoal.set(0, 0.3 + f * 2.1, 0);
  }
  highlight(ids: string | string[]): void {
    const set = Array.isArray(ids) ? ids : [ids];
    this.parts.forEach((p) => {
      (p.userData as unknown as PartUserData).emiGoal = set.includes(
        (p.userData as unknown as PartUserData).partId,
      )
        ? 0.6
        : 0;
    });
  }
  selectPart(id: string): void {
    this.highlight([id]);
  }
  clearHighlight(): void {
    this.parts.forEach((p) => {
      (p.userData as unknown as PartUserData).emiGoal = 0;
    });
  }
  isolate(ids: string | string[]): void {
    const set = Array.isArray(ids) ? ids : [ids];
    this.parts.forEach((p) => {
      const ud = p.userData as unknown as PartUserData;
      const keep = set.includes(ud.partId);
      ud.opGoal = keep ? 1 : 0.06;
      ud.emiGoal = keep ? 0.5 : 0;
    });
  }
  clearIsolate(): void {
    this.parts.forEach((p) => {
      (p.userData as unknown as PartUserData).opGoal = 1;
    });
  }
  focus(id: string): void {
    const p = this.parts.find(
      (x) => (x.userData as unknown as PartUserData).partId === id,
    );
    if (!p) return;
    const ud = p.userData as unknown as PartUserData;
    const c = ud.home.clone().addScaledVector(ud.spread, this.explodeGoal);
    this.targetGoal.copy(c);
    this.sphTarget.radius = 15;
    this.highlight([id]);
  }
  reset(): void {
    this.explodeGoal = 0;
    this.clearHighlight();
    this.clearIsolate();
    if (this.hasFrameSequence()) {
      this.targetGoal.set(0, 0, 0);
      this.sphTarget.radius = 16;
      this.sphTarget.theta = Math.PI / 2;
      this.sphTarget.phi = Math.PI / 2;
    } else {
      this.targetGoal.set(0, 0.3, 0);
      this.sphTarget.radius = 24;
      this.sphTarget.theta = 0.7;
      this.sphTarget.phi = 1.08;
    }
  }
  setSinglePart(v: boolean): void {
    this.singlePart = v;
    this.parts.forEach((p) => {
      const keep = !v || (p.userData as unknown as PartUserData).partId === "P-02";
      p.visible = keep;
    });
    if (v) {
      this.explodeGoal = 0;
      this.reset();
    }
  }
  setAutoOrbit(v: boolean): void {
    this.autoOrbit = !!v;
  }
  setAccent(hex: string): void {
    this.accent.set(hex);
    this.parts.forEach((p) =>
      p.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh)
          (mesh.material as THREE.MeshStandardMaterial).emissive.copy(
            this.accent,
          );
      }),
    );
  }

  private onResize(): void {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  private loop(): void {
    if (!this._running) return;
    const k = this.reduced ? 1 : 0.14;
    if (this.autoOrbit && !this._dragging) {
      this.sph.theta += 0.0018;
      this.sphTarget.theta = this.sph.theta;
    }
    this.explode += (this.explodeGoal - this.explode) * k;
    this.sph.radius += (this.sphTarget.radius - this.sph.radius) * k;
    this.target.lerp(this.targetGoal, k);

    for (const p of this.parts) {
      const u = p.userData as unknown as PartUserData;
      p.position.copy(u.home).addScaledVector(u.spread, this.explode);
      u.emi += (u.emiGoal - u.emi) * k;
      u.op += (u.opGoal - u.op) * k;
      p.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        const mat = mesh.material as THREE.MeshStandardMaterial;
        mat.emissiveIntensity = u.emi;
        const base =
          mesh.geometry.type === "CylinderGeometry" &&
          mat.side === THREE.DoubleSide
            ? 0.32
            : 1;
        mat.opacity = u.op < 0.999 ? u.op : base;
      });
    }

    // Update frame sequence: scrub explode frames + depth displacement + labels
    if (this.frameSeq && this.frameSeq.loaded) {
      const fs = this.frameSeq;

      // Scrub explode frames based on explode slider
      if (fs.count > 1) {
        const idx = Math.min(
          Math.floor(this.explode * fs.count),
          fs.count - 1,
        );
        const tex = fs.textures[idx];
        if (tex && fs.mat.map !== tex) {
          fs.mat.map = tex;
          fs.mat.needsUpdate = true;
          fs.depthData = this._estimateDepth(tex);
        }
      }

      // Apply depth displacement (always update for smooth animation)
      this._applyDepth(this.explode);

      // Animate part labels: fade in and move outward as explode increases
      const labelOpacity = Math.min(1, Math.max(0, (this.explode - 0.15) / 0.25));
      fs.partLabels.forEach((p) => {
        p.sprite.material.opacity = labelOpacity;
        p.sprite.position.copy(p.homeOffset).multiplyScalar(0.5 + this.explode * 0.8);
      });
    }

    const s = this.sph;
    const x = s.radius * Math.sin(s.phi) * Math.sin(s.theta);
    const y = s.radius * Math.cos(s.phi);
    const z = s.radius * Math.sin(s.phi) * Math.cos(s.theta);
    this.camera.position.set(
      x + this.target.x,
      y + this.target.y,
      z + this.target.z,
    );
    this.camera.lookAt(this.target);

    this.renderer.render(this.scene, this.camera);
    requestAnimationFrame(this.loop);
  }

  dispose(): void {
    this._running = false;
    window.removeEventListener("resize", this._onResize);
    if (this._ro) this._ro.disconnect();
    this.clearFrameSequence();
    this.renderer.dispose();
    if (this.renderer.domElement.parentNode)
      this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
  }
}
