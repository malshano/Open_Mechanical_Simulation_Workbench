// 3D viewer: CAD faces (pickable), mesh/result overlays, rotors, orientation triad.
import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';

export function turbo(t) {
  t = Math.min(1, Math.max(0, t));
  const r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
  const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
  const b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
  return [Math.min(1, Math.max(0, r)), Math.min(1, Math.max(0, g)), Math.min(1, Math.max(0, b))];
}

function b64(s, Type) {
  const bin = atob(s);
  const buf = new ArrayBuffer(bin.length);
  const u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Type(buf);
}

export class Viewer {
  constructor(canvas, { onPick, onHover } = {}) {
    this.canvas = canvas;
    this.onPick = onPick; this.onHover = onHover;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.localClippingEnabled = true;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 1e-4, 1e4);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(1, -1.4, 0.9);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.15;
    this.controls.screenSpacePanning = true;
    const hemi = new THREE.HemisphereLight(0xffffff, 0xb8bfcc, 1.9); hemi.position.set(0, 0, 1); this.scene.add(hemi);
    this.key = new THREE.DirectionalLight(0xffffff, 1.6);
    this.camera.add(this.key); this.key.position.set(0.5, 1, 0.3);
    this.scene.add(this.camera);
    this.geoGroup = new THREE.Group(); this.scene.add(this.geoGroup);
    this.overlayGroup = new THREE.Group(); this.scene.add(this.overlayGroup);
    this.rotorGroup = new THREE.Group(); this.scene.add(this.rotorGroup);
    this.faces = []; this.faceById = new Map();
    this.meshes = []; // {mesh, triFace}
    this.pickFilter = null;
    this.hoverFace = null;
    this.colorFn = () => [0.75, 0.77, 0.8];
    this.showAir = false; this.showEdges = true; this.clipPlanes = [];
    this._initTriad();
    this._bindEvents();
    this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas.parentElement);
    const loop = () => { this.controls.update(); this._animate(); this._syncMirrors(); this._render(); requestAnimationFrame(loop); };
    loop();
  }

  _initTriad() {
    this.triScene = new THREE.Scene();
    this.triCam = new THREE.OrthographicCamera(-1.6, 1.6, 1.6, -1.6, 0.1, 10);
    this.triCam.up.set(0, 0, 1);
    const mk = (dir, color, label) => {
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(...dir), new THREE.Vector3(), 1, color, 0.3, 0.18);
      this.triScene.add(arrow);
      const c = document.createElement('canvas'); c.width = c.height = 64;
      const g = c.getContext('2d'); g.fillStyle = '#' + color.toString(16).padStart(6, '0');
      g.font = 'bold 44px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(label, 32, 34);
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
      sp.position.set(...dir.map(v => v * 1.35)); sp.scale.set(0.5, 0.5, 0.5);
      this.triScene.add(sp);
    };
    mk([1, 0, 0], 0xe23b3b, 'X'); mk([0, 1, 0], 0x22a34a, 'Y'); mk([0, 0, 1], 0x2f6fe0, 'Z');
  }

  _resize() {
    const p = this.canvas.parentElement;
    const w = p.clientWidth, h = p.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    // a panel over the bottom of the view (messages dock): the picture is centred in the part above it. The
    // camera sees that part as its whole view; the canvas shows it plus the strip behind the panel.
    const vh = Math.max(h - (this.inset || 0), 60);
    this.camera.aspect = w / vh;
    if (vh < h) this.camera.setViewOffset(w, vh, 0, 0, w, h); else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
  }

  // pixels at the bottom of the view covered by a panel (0 = none)
  setInset(px) {
    px = Math.max(0, Math.round(px || 0));
    if (px === (this.inset || 0)) return;
    this.inset = px; this._resize();
  }

  // the view as a picture (a video frame): the 3D canvas on the viewport's background gradient
  snapshot(bg = ['#5b6f8a', '#c9d3df']) {
    this._forceDepth = true;  // (the cloud's depth pass is redone for every picture)
    this._render();
    const c = this.canvas, out = document.createElement('canvas');
    out.width = c.width; out.height = c.height;
    const g = out.getContext('2d'), gr = g.createLinearGradient(0, 0, 0, out.height);
    gr.addColorStop(0, bg[0]); gr.addColorStop(1, bg[1]);
    g.fillStyle = gr; g.fillRect(0, 0, out.width, out.height);
    g.drawImage(c, 0, 0);  // (same task as the render: the drawing buffer is still there)
    return out;
  }

  _render() {
    const r = this.renderer;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    r.setViewport(0, 0, w, h); r.setScissorTest(false);
    this._volumeDepth(w, h, this._forceDepth); this._forceDepth = false;
    r.render(this.scene, this.camera);
    // triad
    const s = 90, ty = 8 + (this.inset || 0);  // (above a panel that covers the bottom of the view)
    r.setScissorTest(true); r.setScissor(8, ty, s, s); r.setViewport(8, ty, s, s);
    const d = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize().multiplyScalar(4);
    this.triCam.position.copy(d); this.triCam.up.copy(this.camera.up); this.triCam.lookAt(0, 0, 0);
    r.autoClear = false; r.clearDepth(); r.render(this.triScene, this.triCam); r.autoClear = true;
    r.setScissorTest(false);
  }

  _bindEvents() {
    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let down = null;
    const pick = (ev) => {
      const rect = this.canvas.getBoundingClientRect();
      ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      ray.setFromCamera(ndc, this.camera);
      const objs = this.meshes.filter(m => m.mesh.visible).map(m => m.mesh);
      // a section plane removes what is in front of it: those faces cannot be clicked either
      const hits = ray.intersectObjects(objs, false).filter(h => this.clipPlanes.every(pl => pl.distanceToPoint(h.point) >= -1e-9));
      // only the face you can see counts (no silent click-through); translucent air faces are skipped
      // when an opaque face is behind them and the active condition cannot go on air faces
      for (const h of hits) {
        const rec = this.meshes.find(m => m.mesh === h.object);
        const f = this.faceById.get(rec.triFace[h.faceIndex]);
        const ok = !this.pickFilter || this.pickFilter(f);
        if (rec.air && !ok && hits.some(x => !this.meshes.find(m => m.mesh === x.object).air)) continue;
        return { face: f, point: h.point, allowed: ok };
      }
      return null;
    };
    this.canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    this.canvas.addEventListener('pointerup', e => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
      down = null;
      if (moved > 4 || e.button !== 0) return;
      if (this.probeMode) { this.onProbe && this.onProbe(this.probeAt(e)); return; }
      if (this.edgePick) { this._edgeHover(e); if (this.edgePick.hover) { this.edgePick.onPick(this.edgePick.hover); return; } }
      const p = pick(e);
      this.onPick && this.onPick(p ? p.face : null, e, p ? p.allowed : true, p ? p.point.toArray() : null);
    });
    let last = 0;
    this.canvas.addEventListener('pointermove', e => {
      const now = performance.now();
      if (now - last < 40 || down) return; last = now;
      if (this.edgePick) this._edgeHover(e);
      const p = this.geoGroup.visible ? pick(e) : null;
      const fid = p ? p.face.id : null;
      if (fid !== this.hoverFace) { this.hoverFace = fid; this.refreshColors(); }
      this.onHover && this.onHover(p ? p.face : null, e);
    });
    this.canvas.addEventListener('pointerleave', () => {
      this.hoverFace = null; this.refreshColors(); this.onHover && this.onHover(null);
    });
    window.addEventListener('keydown', e => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'f' || e.key === 'F') this.fit();
    });
  }

  clearGeometry() {
    for (const c of [...this.geoGroup.children]) { this.geoGroup.remove(c); c.geometry?.dispose(); }
    if (this.previewR) this.setPreviewRotation(null);
    this.meshes = []; this.faces = []; this.faceById = new Map();
  }

  // faces: [{id, kind, mesh:{vertices, triangles}}]; airKinds: kinds drawn translucent
  setGeometry(faces, { airKinds = [], hiddenKinds = [] } = {}) {
    this.clearGeometry();
    this.faces = faces;
    for (const f of faces) this.faceById.set(f.id, f);
    const groups = { solid: [], air: [] };
    for (const f of faces) {
      if (hiddenKinds.includes(f.kind)) groups.air.push(f);
      else if (airKinds.includes(f.kind)) groups.air.push(f);
      else groups.solid.push(f);
    }
    for (const [key, list] of Object.entries(groups)) {
      if (!list.length) continue;
      let nv = 0, nt = 0;
      for (const f of list) { nv += f.mesh.vertices.length / 3; nt += f.mesh.triangles.length / 3; }
      const pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3), idx = new Uint32Array(nt * 3);
      const triFace = new Int32Array(nt), vertFace = new Int32Array(nv);
      const edgePts = [];
      let vo = 0, to = 0;
      for (const f of list) {
        const v = f.mesh.vertices, t = f.mesh.triangles;
        pos.set(v, vo * 3);
        for (let i = 0; i < v.length / 3; i++) vertFace[vo + i] = f.id;
        for (let i = 0; i < t.length; i++) idx[to * 3 + i] = t[i] + vo;
        for (let i = 0; i < t.length / 3; i++) triFace[to + i] = f.id;
        // boundary edges of this face = edges used by exactly one of its triangles
        const cnt = new Map();
        for (let i = 0; i < t.length; i += 3) {
          for (const [a, b] of [[t[i], t[i + 1]], [t[i + 1], t[i + 2]], [t[i + 2], t[i]]]) {
            const k = a < b ? a * 1e7 + b : b * 1e7 + a;
            cnt.set(k, (cnt.get(k) || 0) + 1);
          }
        }
        for (const [k, c] of cnt) {
          if (c !== 1) continue;
          const a = Math.floor(k / 1e7), b = k % 1e7;
          edgePts.push(v[a * 3], v[a * 3 + 1], v[a * 3 + 2], v[b * 3], v[b * 3 + 1], v[b * 3 + 2]);
        }
        vo += v.length / 3; to += t.length / 3;
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setAttribute('color', new THREE.BufferAttribute(col, 3));
      g.setIndex(new THREE.BufferAttribute(idx, 1));
      g.computeVertexNormals();
      const air = key === 'air';
      const m = new THREE.MeshStandardMaterial({
        vertexColors: true, roughness: 0.65, metalness: 0.05, side: THREE.DoubleSide,
        transparent: air, opacity: air ? 0.22 : 1, depthWrite: !air,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      });
      const mesh = new THREE.Mesh(g, m);
      mesh.renderOrder = air ? 2 : 0;
      this.geoGroup.add(mesh);
      const eg = new THREE.BufferGeometry();
      eg.setAttribute('position', new THREE.Float32BufferAttribute(edgePts, 3));
      const edges = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({
        color: air ? 0x5b8cff : 0x1d2330, transparent: true, opacity: air ? 0.5 : 0.55 }));
      edges.userData.isEdges = true; edges.userData.air = air;
      this.geoGroup.add(edges);
      this.meshes.push({ mesh, triFace, vertFace, air, edges });
    }
    this._addSectionCaps(faces, [...airKinds, ...hiddenKinds]);
    this.applyVisibility();
    this.refreshColors();
  }

  // The air volume is cut at a symmetry plane, and a part that crosses the plane is open there: seen from the cut side
  // it looks hollow. The air's face on the plane has a hole where each part is cut; fill those holes (display only, the
  // mesh and the conditions are not touched). Loops of the face's boundary: depth 0 = the outline of the air, depth 1 =
  // a part's section (filled), depth 2 = an opening inside that section (left open), and so on.
  _addSectionCaps(faces, airKinds) {
    this.caps = [];
    for (const f of faces) {
      if (f.symmetry === undefined || !airKinds.includes(f.kind) || !f.mesh?.triangles?.length) continue;
      const v = f.mesh.vertices, t = f.mesh.triangles;
      const n = f.normal || [0, 0, 1], drop = [0, 1, 2].reduce((a, i) => Math.abs(n[i]) > Math.abs(n[a]) ? i : a, 0);
      const [iu, iv] = [0, 1, 2].filter(i => i !== drop);
      const cnt = new Map();
      for (let i = 0; i < t.length; i += 3) {
        for (const [a, b] of [[t[i], t[i + 1]], [t[i + 1], t[i + 2]], [t[i + 2], t[i]]]) {
          const k = a < b ? a * 1e7 + b : b * 1e7 + a;
          cnt.set(k, (cnt.get(k) || 0) + 1);
        }
      }
      const adj = new Map();
      for (const [k, c] of cnt) {
        if (c !== 1) continue;
        const a = Math.floor(k / 1e7), b = k % 1e7;
        (adj.get(a) || adj.set(a, []).get(a)).push(b); (adj.get(b) || adj.set(b, []).get(b)).push(a);
      }
      const loops = [], seen = new Set();
      for (const s of adj.keys()) {
        if (seen.has(s)) continue;
        const loop = [s]; seen.add(s);
        let prev = -1, cur = s;
        for (;;) {
          const nb = adj.get(cur).filter(x => x !== prev && !(x === s && loop.length < 3));
          const nx = nb.find(x => !seen.has(x));
          if (nx === undefined) break;
          loop.push(nx); seen.add(nx); prev = cur; cur = nx;
        }
        if (loop.length >= 3) loops.push(loop);
      }
      if (loops.length < 2) continue;
      const pts = loops.map(l => l.map(i => new THREE.Vector2(v[i * 3 + iu], v[i * 3 + iv])));
      const inside = (p, poly) => {
        let c = false;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
          const a = poly[i], b = poly[j];
          if ((a.y > p.y) !== (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) c = !c;
        }
        return c;
      };
      const parents = pts.map((p, i) => pts.map((q, j) => j !== i && inside(p[0], q) ? j : -1).filter(j => j >= 0));
      const depth = parents.map(p => p.length);
      const area = poly => Math.abs(THREE.ShapeUtils.area(poly));
      const plane = v[loops[0][0] * 3 + drop];
      const pos = [], idx = [];
      loops.forEach((l, i) => {
        if (depth[i] % 2 !== 1) return;                                      // (a part's section is an odd depth)
        const holes = loops.map((_, j) => j).filter(j => depth[j] === depth[i] + 1 && parents[j].includes(i));
        const contour = pts[i], hp = holes.map(j => pts[j]);
        if (area(contour) < 1e-12) return;
        let tris;
        try { tris = THREE.ShapeUtils.triangulateShape(contour, hp); } catch (e) { return; }
        const all = contour.concat(...hp), base = pos.length / 3;
        for (const p of all) { const q = [0, 0, 0]; q[iu] = p.x; q[iv] = p.y; q[drop] = plane; pos.push(...q); }
        for (const tr of tris) idx.push(base + tr[0], base + tr[1], base + tr[2]);
      });
      if (!idx.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx); g.computeVertexNormals();
      const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
        color: 0x8d99ae, roughness: 0.7, metalness: 0.05, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
      mesh.userData.isCap = true;
      this.geoGroup.add(mesh); this.caps.push(mesh);
    }
  }

  applyVisibility() {
    for (const m of this.meshes) {
      const vis = !m.air || this.showAir;  // (air faces: the box / far field around the parts)
      m.mesh.visible = vis;
      m.edges.visible = vis && this.showEdges;
    }
  }

  // cut the CAD geometry at a section plane (keeps the part behind the plane, as seen from +axis)
  setGeometryClip(axis, pos) {
    const planes = axis ? [new THREE.Plane(new THREE.Vector3(...[0, 1, 2].map(i => i === 'xyz'.indexOf(axis) ? -1 : 0)), pos)] : [];
    this.clipPlanes = planes;
    for (const m of this.meshes) {
      m.mesh.material.clippingPlanes = planes;
      m.edges.material.clippingPlanes = planes;
      m.mesh.material.needsUpdate = true;
    }
    for (const c of this.caps || []) { c.material.clippingPlanes = planes; c.material.needsUpdate = true; }
  }

  setShowAir(v) { this.showAir = v; this.applyVisibility(); }
  setShowEdges(v) { this.showEdges = v; this.applyVisibility(); }
  showGeometry(v) { this.geoGroup.visible = v; }

  // rotating zones of a sliding-mesh result: the CAD parts inside a zone turn with it, so a section or a video
  // shows the propeller where it is at that time. zones: [{center, axis (unit vector), radius, length, omega
  // (rad/s)}]; angle = omega * t about the axis through the centre, right-handed (as OpenFOAM's rotatingMotion:
  // checked against the moved mesh points of a run). null: back to the stored pose.
  setSpinZones(zones) {
    if (this._spinZones) this.spin(0);
    this._spinZones = zones?.length ? zones : null;
  }
  spin(t) {  // CAD parts at time t (s)
    if (!this._spinZones && !t) return;
    for (const m of this.meshes) {
      if (m.air) continue;
      this._spinGeom(m.mesh.geometry, t);
      this._spinGeom(m.edges.geometry, t);
    }
  }
  spinOverlay(dt) {  // result surface (drawn at a saved time) turned on by dt more seconds: in-between video pictures
    const g = this.overlayGroup.children[0]?.geometry;
    if (g) this._spinGeom(g, dt);
  }
  _spinGeom(g, t) {
    const pa = g.attributes.position, zs = this._spinZones || [];
    let u = g.userData.spin;
    if (!u) {
      if (!zs.length) return;
      const B = pa.array.slice(), n = B.length / 3, zone = new Int8Array(n).fill(-1);
      zs.forEach((z, zi) => {
        const [cx, cy, cz] = z.center, [ax, ay, az] = z.axis, hl = 0.5 * z.length * 1.02, r2 = (z.radius * 1.02) ** 2;
        for (let i = 0; i < n; i++) {
          const dx = B[3 * i] - cx, dy = B[3 * i + 1] - cy, dz = B[3 * i + 2] - cz, h = dx * ax + dy * ay + dz * az;
          if (Math.abs(h) <= hl && dx * dx + dy * dy + dz * dz - h * h <= r2) zone[i] = zi;
        }
      });
      if (!zone.some(v => v >= 0)) return;
      u = g.userData.spin = { B, zone, N: g.attributes.normal?.array.slice() };
    }
    const P = pa.array, B = u.B, rot = zs.map(z => [z, Math.cos(z.omega * t), Math.sin(z.omega * t)]);
    const turn = (src, dst, i, c0, [z, c, s]) => {  // Rodrigues: v c + (a x v) s + a (a.v)(1 - c)
      const [ax, ay, az] = z.axis, x = src[3 * i] - c0[0], y = src[3 * i + 1] - c0[1], w = src[3 * i + 2] - c0[2];
      const d = (ax * x + ay * y + az * w) * (1 - c);
      dst[3 * i] = c0[0] + x * c + (ay * w - az * y) * s + ax * d;
      dst[3 * i + 1] = c0[1] + y * c + (az * x - ax * w) * s + ay * d;
      dst[3 * i + 2] = c0[2] + w * c + (ax * y - ay * x) * s + az * d;
    };
    const O = [0, 0, 0], NA = g.attributes.normal?.array;
    for (let i = 0; i < u.zone.length; i++) {
      const zi = u.zone[i];
      if (zi < 0 || !rot[zi]) continue;
      turn(B, P, i, rot[zi][0].center, rot[zi]);
      if (NA && u.N) turn(u.N, NA, i, O, rot[zi]);
    }
    pa.needsUpdate = true;
    if (NA) g.attributes.normal.needsUpdate = true;
    g.computeBoundingSphere();
  }

  // live orientation preview: turn the parts (and rotors / zones on them) by a 3x3 matrix, the air box and the
  // flow arrows stay; null = back to the stored orientation
  // The turn is about `center` (the middle of the parts), which stays where it is: x' = R (x - c) + c.
  setPreviewRotation(R, center = [0, 0, 0]) {
    const m = new THREE.Matrix4();
    if (R) {
      const c = center, t = [0, 1, 2].map(i => c[i] - (R[i][0] * c[0] + R[i][1] * c[1] + R[i][2] * c[2]));
      m.set(R[0][0], R[0][1], R[0][2], t[0], R[1][0], R[1][1], R[1][2], t[1], R[2][0], R[2][1], R[2][2], t[2], 0, 0, 0, 1);
    }
    const objs = [...this.meshes.filter(e => !e.air).flatMap(e => [e.mesh, e.edges]), this.rotorGroup, this.zoneGroup].filter(Boolean);
    for (const o of objs) { o.matrixAutoUpdate = false; o.matrix.copy(m); o.matrixWorldNeedsUpdate = true; }
    this.previewR = R;
  }

  setColorFn(fn) { this.colorFn = fn; this.refreshColors(); }

  refreshColors() {
    for (const m of this.meshes) {
      const col = m.mesh.geometry.attributes.color;
      const arr = col.array;
      let lastF = null, c = null;
      for (let i = 0; i < m.vertFace.length; i++) {
        const fid = m.vertFace[i];
        if (fid !== lastF) {
          c = this.colorFn(this.faceById.get(fid));
          if (fid === this.hoverFace) c = c.map(x => Math.min(1, x * 0.75 + 0.3));
          if (this._flashOn && this._flashSet && this._flashSet.has(fid)) c = [1, 0.85, 0.1];
          lastF = fid;
        }
        arr[i * 3] = c[0]; arr[i * 3 + 1] = c[1]; arr[i * 3 + 2] = c[2];
      }
      col.needsUpdate = true;
    }
  }

  flash(faceIds) {
    this._flashSet = new Set(faceIds);
    clearTimeout(this._flashT);
    let n = 0;
    const tick = () => {
      this._flashOn = n % 2 === 0 && n < 7;
      this.refreshColors();
      if (n++ < 7) this._flashT = setTimeout(tick, 300); else this._flashSet = null;
    };
    tick();
  }

  bounds(onlyVisible = true) {
    const box = new THREE.Box3();
    for (const m of this.meshes) {
      if (onlyVisible && !m.mesh.visible) continue;
      m.mesh.geometry.computeBoundingBox();
      box.union(m.mesh.geometry.boundingBox);
    }
    if (box.isEmpty()) {
      for (const c of this.overlayGroup.children) { if (c.geometry) { c.geometry.computeBoundingBox(); box.union(c.geometry.boundingBox); } }
    }
    if (this._mirror?.on && !box.isEmpty()) {  // the mirror images are part of what is shown
      const b0 = box.clone();
      for (const M of this._mirror.mats) box.union(b0.clone().applyMatrix4(M));
    }
    return box;
  }

  fitTo(min, max) {  // zoom to a box (e.g. the whole air volume)
    this._fitBox = new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max));
    this.fit(); this._fitBox = null;
  }

  fit(dir) {
    const box = this._fitBox || this.bounds();
    if (!this._fitBox && this.bcGroup && this.geoGroup.visible && !box.isEmpty())
      this.bcGroup.children.forEach(m => { if (m.isMesh && !m.userData.far) box.expandByObject(m); });
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    let d = dir ? new THREE.Vector3(...dir) : new THREE.Vector3().subVectors(this.camera.position, this.controls.target);
    if (d.lengthSq() < 1e-12) d.set(1, -1.4, 0.9);
    d.normalize();
    const dist = r / Math.sin((this.camera.fov * Math.PI / 180) / 2) * 1.05;
    this.controls.target.copy(c);
    this.camera.position.copy(c).addScaledVector(d, dist);
    this.camera.near = dist / 1000; this.camera.far = dist * 100; this.camera.updateProjectionMatrix();
    if (Math.abs(d.z) > 0.99) this.camera.up.set(0, 1, 0); else this.camera.up.set(0, 0, 1);
    this.controls.update();
  }

  _animate() {
    if (this._particles) this._stepParticles();
    const a = this._anim;
    if (!a) return;
    const s = a.amp * Math.sin((performance.now() - a.t0) / 1000 * 2 * Math.PI * 0.8);  // 0.8 cycles per second
    const P = a.attr.array, B = a.base, D = a.D;
    for (let i = 0; i < P.length; i++) P[i] = B[i] + s * D[i];
    a.attr.needsUpdate = true;
  }

  setView(name) {
    // clicking the same axis twice looks from the opposite side
    const dirs = { iso: [1, -1.4, 0.9], '+x': [1, 0, 0], '+y': [0, 1, 0], '+z': [0, 0, 1] };
    if (name === 'fit') return this.fit();
    let d = dirs[name];
    if (name !== 'iso' && this._lastView === name) { d = d.map(v => -v); this._lastView = '-' + name; }
    else this._lastView = name;
    this.fit(d);
    return this._lastView;
  }

  // ---------------------------------------------------------------- mirror images (symmetry planes)
  // planes: [{point, normal}] (as the project stores them); on: draw the mirror images of the model and of every
  // result drawn: one image for each non-empty subset of the planes. The images share the geometry and materials of
  // the originals and follow them every frame (position, visibility, animation, a turning propeller turns the
  // mirrored way because the whole image is reflected).
  setMirrors(planes, on) {
    const key = JSON.stringify([planes || [], !!on]);
    if (key === this._mirrorKey) return;
    this._mirrorKey = key;
    this._mirror = { on: !!on && !!(planes && planes.length), mats: [] };
    if (this._mirror.on) {
      const refl = planes.map(pl => {
        const n = new THREE.Vector3(...pl.normal).normalize(), d = 2 * n.dot(new THREE.Vector3(...pl.point));
        return new THREE.Matrix4().set(
          1 - 2 * n.x * n.x, -2 * n.x * n.y, -2 * n.x * n.z, d * n.x,
          -2 * n.x * n.y, 1 - 2 * n.y * n.y, -2 * n.y * n.z, d * n.y,
          -2 * n.x * n.z, -2 * n.y * n.z, 1 - 2 * n.z * n.z, d * n.z,
          0, 0, 0, 1);
      });
      for (let mask = 1; mask < (1 << refl.length); mask++) {
        const M = new THREE.Matrix4();
        for (let i = 0; i < refl.length; i++) if (mask & (1 << i)) M.premultiply(refl[i]);
        this._mirror.mats.push(M);
      }
    }
    this._mirrorSig = null;  // (rebuilt on the next frame)
  }
  _mirrorSources() {
    const out = [];
    for (const e of this.meshes) if (!e.air) { out.push(e.mesh); if (e.edges) out.push(e.edges); }
    out.push(...this.overlayGroup.children);
    if (this.isoMesh) out.push(this.isoMesh);
    if (this.contourGroup) out.push(...this.contourGroup.children);
    for (const o of [this.volumeMesh, this.arrowMesh, this.particleObj]) if (o) out.push(o);
    return out;
  }
  _syncMirrors() {
    const m = this._mirror;
    if (!m?.on) {
      if (this.mirrorGroup?.children.length) { for (const c of [...this.mirrorGroup.children]) this.mirrorGroup.remove(c); this._mirrorPairs = []; this._mirrorSig = null; }
      return;
    }
    const src = this._mirrorSources(), sig = src.map(o => o.uuid).join(',') + '|' + m.mats.length;
    if (sig !== this._mirrorSig) {
      this._mirrorSig = sig;
      if (!this.mirrorGroup) { this.mirrorGroup = new THREE.Group(); this.scene.add(this.mirrorGroup); }
      for (const c of [...this.mirrorGroup.children]) this.mirrorGroup.remove(c);  // (clones share geometry: nothing to dispose)
      this._mirrorPairs = [];
      for (const M of m.mats) {
        const g = new THREE.Group(); g.matrixAutoUpdate = false; g.matrix.copy(M); g.matrixWorldNeedsUpdate = true;
        for (const o of src) {
          const c = o.clone(true); c.matrixAutoUpdate = false; c.userData = { mirror: true };
          g.add(c); this._mirrorPairs.push([o, c]);
        }
        this.mirrorGroup.add(g);
      }
    }
    for (const [o, c] of this._mirrorPairs) {
      c.matrix.copy(o.matrixWorld); c.matrixWorldNeedsUpdate = true;  // (the source's place: the group reflects it)
      let vis = true;
      for (let q = o; q && q !== this.scene; q = q.parent) vis = vis && q.visible;
      c.visible = vis;
    }
  }

  // ---------------------------------------------------------------- circular edge picking
  // edges: [{id, points (flat xyz along the edge), closed, center, axis, radius, ...}]; the edges are drawn thin, the one
  // within 10 px of the pointer (and not hidden behind the model) lights up; a click on it calls onPick(edge)
  setEdgePick(edges, onPick) {
    if (!this.edgeGroup) { this.edgeGroup = new THREE.Group(); this.scene.add(this.edgeGroup); }
    for (const c of [...this.edgeGroup.children]) { this.edgeGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
    this.edgePick = null;
    if (!edges) return;
    const mat = new THREE.LineBasicMaterial({ color: 0xff9f1c });
    for (const e of edges) {
      const g = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(e.points, 3));
      this.edgeGroup.add(e.closed ? new THREE.LineLoop(g, mat) : new THREE.Line(g, mat));
    }
    this.edgePick = { edges, onPick, hover: null, tube: null };
  }
  _edgeHover(ev) {
    const ep = this.edgePick; if (!ep) return;
    const rect = this.canvas.getBoundingClientRect(), mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const v = new THREE.Vector3(), cands = [];
    for (const e of ep.edges) {
      const P = e.points, n = P.length / 3, pr = [];
      for (let i = 0; i < n; i++) {
        v.set(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]).project(this.camera);
        pr.push([(v.x * 0.5 + 0.5) * rect.width, (-v.y * 0.5 + 0.5) * rect.height, v.z]);
      }
      let best = 1e9, bi = 0;
      for (let i = 0; i < n - (e.closed ? 0 : 1); i++) {
        const a = pr[i], b = pr[(i + 1) % n];
        if (a[2] > 1 || b[2] > 1) continue;  // behind the camera
        const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
        const t = l2 > 0 ? Math.max(0, Math.min(1, ((mx - a[0]) * dx + (my - a[1]) * dy) / l2)) : 0;
        const d = Math.hypot(mx - (a[0] + t * dx), my - (a[1] + t * dy));
        if (d < best) { best = d; bi = i; }
      }
      if (best < 10) cands.push({ e, d: best, i: bi });
    }
    cands.sort((a, b) => a.d - b.d);
    // an edge on the far side of the model is not pickable: a ray from the camera to its nearest point must not hit anything first
    const solids = this.meshes.filter(m => m.mesh.visible && !m.air).map(m => m.mesh), ray = new THREE.Raycaster();
    let hit = null;
    for (const c of cands.slice(0, 4)) {
      const P = c.e.points, i = c.i, q = new THREE.Vector3(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]);
      const dir = q.clone().sub(this.camera.position), dist = dir.length();
      ray.set(this.camera.position, dir.normalize());
      const h = ray.intersectObjects(solids, false)[0];
      if (!h || h.distance > dist - 0.01 * dist - 1e-4) { hit = c.e; break; }
    }
    if ((hit?.id ?? null) === (ep.hover?.id ?? null)) return;
    ep.hover = hit;
    if (ep.tube) { this.edgeGroup.remove(ep.tube); ep.tube.geometry.dispose(); ep.tube.material.dispose(); ep.tube = null; }
    this.canvas.style.cursor = hit ? 'pointer' : 'crosshair';
    if (hit) {
      const pts = []; for (let i = 0; i < hit.points.length; i += 3) pts.push(new THREE.Vector3(hit.points[i], hit.points[i + 1], hit.points[i + 2]));
      const curve = new THREE.CatmullRomCurve3(pts, hit.closed);  // (a thin tube: a few pixels wide whatever the size of the edge)
      ep.tube = new THREE.Mesh(new THREE.TubeGeometry(curve, Math.max(24, pts.length * 2), Math.max(0.05 * hit.radius, 1e-5), 6, hit.closed),
        new THREE.MeshBasicMaterial({ color: 0xffd43b, depthTest: false }));
      ep.tube.renderOrder = 15; this.edgeGroup.add(ep.tube);
    }
  }

  // the circle and axis found on a round feature: {center, axis, radius, length} or null
  setAxisMarker(m) {
    if (!this.axisGroup) { this.axisGroup = new THREE.Group(); this.scene.add(this.axisGroup); }
    for (const c of [...this.axisGroup.children]) { this.axisGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
    if (!m) return;
    const c = new THREE.Vector3(...m.center), a = new THREE.Vector3(...m.axis).normalize();
    const u = new THREE.Vector3().crossVectors(a, Math.abs(a.x) < 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0)).normalize();
    const w = new THREE.Vector3().crossVectors(a, u), R = Math.max(m.radius || 0, 1e-6), half = Math.max(m.length || 0, 4 * R) * 0.75;
    const ring = [];
    for (let i = 0; i < 72; i++) { const t = 2 * Math.PI * i / 72; ring.push(c.clone().addScaledVector(u, R * Math.cos(t)).addScaledVector(w, R * Math.sin(t))); }
    const mat = () => new THREE.LineBasicMaterial({ color: 0xffd43b, depthTest: false });
    const loop = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(ring), mat());
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([c.clone().addScaledVector(a, -half), c.clone().addScaledVector(a, half)]), mat());
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.12 * R + 1e-5, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffd43b, depthTest: false }));
    dot.position.copy(c);
    for (const o of [loop, line, dot]) { o.renderOrder = 14; this.axisGroup.add(o); }
  }

  // an arrow on the face that is being aligned: {point, dir, length} or null
  setAlignMarker(m) {
    if (!this.alignGroup) { this.alignGroup = new THREE.Group(); this.scene.add(this.alignGroup); }
    for (const c of [...this.alignGroup.children]) { this.alignGroup.remove(c); c.traverse?.(o => { o.geometry?.dispose(); o.material?.dispose(); }); }
    if (!m) return;
    const a = new THREE.ArrowHelper(new THREE.Vector3(...m.dir).normalize(), new THREE.Vector3(...m.point), m.length, 0xffd43b, m.length * 0.3, m.length * 0.16);
    a.traverse(o => { if (o.material) o.material.depthTest = false; o.renderOrder = 13; });
    this.alignGroup.add(a);
  }

  // ---------------------------------------------------------------- rotation gizmo
  // Three rings around `center` (x red, y green, z blue), fixed in the world. Dragging a ring turns the preview about that
  // axis: onRotate(axis 0..2, angle in radians, final) - first with angle 0 when the drag starts. Shift snaps to 5 degrees.
  setRotateGizmo(opts) {
    if (!this.rotGroup) { this.rotGroup = new THREE.Group(); this.scene.add(this.rotGroup); this._bindRotateDrag(); }
    for (const c of [...this.rotGroup.children]) { this.rotGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
    this.rotRings = []; this.rotOpts = opts || null;
    if (!opts) return;
    const R = opts.size * 0.62, tube = opts.size * 0.007, cols = [0xe23b3b, 0x22c55e, 0x3b82f6];
    for (let a = 0; a < 3; a++) {
      const g = new THREE.TorusGeometry(R, tube, 10, 120);
      if (a === 0) g.rotateY(Math.PI / 2); else if (a === 1) g.rotateX(Math.PI / 2);   // (the torus lies in the xy plane)
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: cols[a], transparent: true, opacity: 0.8, depthTest: false }));
      m.position.set(...opts.center); m.renderOrder = 12; m.userData = { axis: a, base: cols[a] };
      this.rotGroup.add(m); this.rotRings.push(m);
    }
  }
  _bindRotateDrag() {
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(), host = this.canvas.parentElement;
    ray.params.Mesh = { threshold: 0 };
    let drag = null;
    const castFrom = ev => {
      const rect = this.canvas.getBoundingClientRect();
      ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, this.camera);
    };
    const hit = ev => {
      if (!this.rotRings?.length || !this.rotGroup.visible) return null;
      castFrom(ev);
      // a ring is thin: pick the one whose circle passes closest to the pointer ray (within a few pixels)
      let best = null, bd = 1e9;
      for (const m of this.rotRings) {
        const a = m.userData.axis, c = new THREE.Vector3(...this.rotOpts.center), n = new THREE.Vector3(); n.setComponent(a, 1);
        const p = new THREE.Vector3();
        if (!ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, c), p)) continue;
        const R = this.rotOpts.size * 0.62, off = Math.abs(p.distanceTo(c) - R);
        const px = off / (2 * Math.tan(this.camera.fov * Math.PI / 360) * Math.max(this.camera.position.distanceTo(p), 1e-9)) * this.canvas.clientHeight;
        if (px < 9 && px < bd) { best = m; bd = px; }
      }
      return best;
    };
    const angleOf = (ev, a) => {
      castFrom(ev);
      const c = new THREE.Vector3(...this.rotOpts.center), n = new THREE.Vector3(); n.setComponent(a, 1);
      const p = new THREE.Vector3();
      if (!ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, c), p)) return null;
      const d = p.sub(c), u = new THREE.Vector3(), v = new THREE.Vector3();
      u.setComponent((a + 1) % 3, 1); v.setComponent((a + 2) % 3, 1);   // (u x v = the axis: right-handed)
      return Math.atan2(d.dot(v), d.dot(u));
    };
    const shade = (m, on) => { for (const r of this.rotRings || []) r.material.opacity = r === m && on ? 1 : on ? 0.35 : 0.8; };
    host.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || ev.target !== this.canvas) return;
      const m = hit(ev);
      if (!m) return;
      const a0 = angleOf(ev, m.userData.axis);
      if (a0 == null) return;
      ev.stopPropagation(); ev.preventDefault();
      drag = { m, axis: m.userData.axis, last: a0, total: 0 };
      shade(m, true);
      this.canvas.setPointerCapture?.(ev.pointerId);
      this.rotOpts.onRotate(drag.axis, 0, false);
    }, true);
    host.addEventListener('pointermove', ev => {
      if (!drag) {
        if (ev.target === this.canvas) { const m = hit(ev); this.canvas.style.cursor = m ? 'grab' : (this.canvas.style.cursor === 'grab' ? '' : this.canvas.style.cursor); shade(m, !!m); }
        return;
      }
      ev.stopPropagation();
      const a = angleOf(ev, drag.axis);
      if (a == null) return;
      let d = a - drag.last; d -= 2 * Math.PI * Math.round(d / (2 * Math.PI));   // (unwrapped: more than a turn is allowed)
      drag.last = a; drag.total += d;
      const step = 5 * Math.PI / 180, ang = ev.shiftKey ? Math.round(drag.total / step) * step : drag.total;
      this.rotOpts.onRotate(drag.axis, ang, false);
    }, true);
    const end = ev => {
      if (!drag) return;
      ev.stopPropagation();
      const d = drag; drag = null; shade(null, false);
      this.canvas.style.cursor = '';
      const step = 5 * Math.PI / 180;
      this.rotOpts.onRotate(d.axis, ev.shiftKey ? Math.round(d.total / step) * step : d.total, true);
    };
    host.addEventListener('pointerup', end, true);
    host.addEventListener('pointercancel', end, true);
  }

  // ---------------------------------------------------------------- symmetry plane editor
  // items: [{id, point, normal (towards the side that is kept), center, size, draft}]. Each is a translucent sheet with
  // an arrow to the kept side and a handle that slides it along its normal; a draft also tints the side that will be
  // removed. onChange(id, point, final) while / when a handle is dragged.
  setSymmetryEditor(items, onChange) {
    if (!this.symGroup) { this.symGroup = new THREE.Group(); this.scene.add(this.symGroup); this._bindSymDrag(); }
    for (const c of [...this.symGroup.children]) {
      this.symGroup.remove(c);
      c.traverse?.(o => { o.geometry?.dispose(); o.material?.dispose(); });
    }
    this.symHandles = []; this.symOnChange = onChange;
    for (const it of items || []) {
      const n = new THREE.Vector3(...it.normal).normalize(), p0 = new THREE.Vector3(...it.point), S = it.size || 1;
      const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
      const col = it.draft ? 0xffb703 : 0x4cc9f0;
      const g = new THREE.Group(); g.userData = { id: it.id };
      const place = o => { o.quaternion.copy(quat); return o; };
      const sheet = place(new THREE.Mesh(new THREE.PlaneGeometry(S, S), new THREE.MeshBasicMaterial({
        color: col, transparent: true, opacity: 0.2, side: THREE.DoubleSide, depthWrite: false })));
      sheet.position.copy(p0); sheet.renderOrder = 8; g.add(sheet);
      const edge = place(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(S, S)),
        new THREE.LineBasicMaterial({ color: col })));
      edge.position.copy(p0); edge.renderOrder = 8; g.add(edge);
      if (it.draft) {  // what the plane will remove: the half-space behind it, over the extent of the sheet
        const rem = place(new THREE.Mesh(new THREE.BoxGeometry(S, S, S * 0.6), new THREE.MeshBasicMaterial({
          color: 0xe63946, transparent: true, opacity: 0.1, depthWrite: false })));
        rem.position.copy(p0).addScaledVector(n, -S * 0.3); rem.renderOrder = 7; g.add(rem);
      }
      const arrow = new THREE.ArrowHelper(n, p0, S * 0.3, 0x2dc653, S * 0.09, S * 0.05);
      arrow.traverse(o => { if (o.material) { o.material.depthTest = false; o.renderOrder = 9; } });
      g.add(arrow);
      const h = new THREE.Mesh(new THREE.SphereGeometry(0.028 * S, 16, 12), new THREE.MeshStandardMaterial({
        color: col, emissive: col, emissiveIntensity: 0.5, depthTest: false }));
      h.position.copy(p0); h.renderOrder = 10; h.userData = { handle: { id: it.id, p: p0.clone(), axis: n.clone(), group: g } };
      g.add(h); this.symHandles.push(h);
      this.symGroup.add(g);
    }
  }
  _bindSymDrag() {
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(), host = this.canvas.parentElement;
    let drag = null;
    const castFrom = ev => {
      const rect = this.canvas.getBoundingClientRect();
      ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, this.camera);
    };
    const along = h => {  // position along the plane normal of the point closest to the mouse ray
      const d = ray.ray.direction, u = h.axis, w0 = ray.ray.origin.clone().sub(h.p), b = d.dot(u), den = 1 - b * b;
      return den < 1e-6 ? null : (u.dot(w0) - b * d.dot(w0)) / den;
    };
    const hit = ev => {
      if (!this.symHandles?.length || !this.symGroup.visible) return null;
      castFrom(ev);
      return ray.intersectObjects(this.symHandles, false)[0]?.object || null;
    };
    host.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || ev.target !== this.canvas) return;
      const m = hit(ev);
      if (!m) return;
      ev.stopPropagation(); ev.preventDefault();
      const h = m.userData.handle;
      drag = { h, t0: along(h) };
      this.canvas.setPointerCapture?.(ev.pointerId);
    }, true);
    host.addEventListener('pointermove', ev => {
      if (!drag) { if (ev.target === this.canvas) { const m = hit(ev); if (m) this.canvas.style.cursor = 'grab'; else if (this.canvas.style.cursor === 'grab') this.canvas.style.cursor = ''; } return; }
      ev.stopPropagation();
      castFrom(ev);
      const t = along(drag.h);
      if (t == null || drag.t0 == null) return;
      drag.h.group.position.copy(drag.h.axis).multiplyScalar(t - drag.t0);
      this.symOnChange && this.symOnChange(drag.h.id, drag.h.p.clone().addScaledVector(drag.h.axis, t - drag.t0).toArray(), false);
    }, true);
    const end = ev => {
      if (!drag) return;
      ev.stopPropagation();
      castFrom(ev);
      const t = along(drag.h), d = drag; drag = null;
      this.canvas.style.cursor = '';
      if (t != null && d.t0 != null) this.symOnChange && this.symOnChange(d.h.id, d.h.p.clone().addScaledVector(d.h.axis, t - d.t0).toArray(), true);
    };
    host.addEventListener('pointerup', end, true);
    host.addEventListener('pointercancel', end, true);
  }

  // value of the shown result under the pointer: {point: [x, y, z], value} or null. The nearest visible point
  // of the result surface (or section) counts; the value is interpolated in its triangle, as the colours are.
  probeAt(ev) {
    const rect = this.canvas.getBoundingClientRect(), ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1,
      -((ev.clientY - rect.top) / rect.height) * 2 + 1), this.camera);
    const objs = this.overlayGroup.children.filter(c => c.userData.probe);
    const h = ray.intersectObjects(objs, false).find(x => this.clipPlanes.every(pl => pl.distanceToPoint(x.point) >= -1e-9));
    if (!h) return null;
    const { val, idx, per } = h.object.userData.probe, t = h.faceIndex;
    let value = val[t];
    if (per === 'vertex') {
      const pos = h.object.geometry.attributes.position, v = i => new THREE.Vector3().fromBufferAttribute(pos, t * 3 + i);
      const b = new THREE.Triangle(v(0), v(1), v(2)).getBarycoord(h.point, new THREE.Vector3());
      value = b.x * val[idx[t * 3]] + b.y * val[idx[t * 3 + 1]] + b.z * val[idx[t * 3 + 2]];
    }
    return Number.isFinite(value) ? { point: h.point.toArray(), value } : null;
  }

  // pinned probe labels: [{point: [x, y, z], text}]
  setProbes(list) {
    if (!this.probeGroup) { this.probeGroup = new THREE.Group(); this.scene.add(this.probeGroup); }
    for (const c of [...this.probeGroup.children]) { this.probeGroup.remove(c); c.material?.map?.dispose(); c.material?.dispose(); }
    for (const pr of list || []) {
      const c = document.createElement('canvas'), g = c.getContext('2d'), fs = 26, dot = 16;
      g.font = `600 ${fs}px sans-serif`;
      c.width = Math.ceil(g.measureText(pr.text).width) + 26 + dot; c.height = fs + 14;
      g.font = `600 ${fs}px sans-serif`;
      g.fillStyle = 'rgba(20,24,32,.86)'; g.fillRect(dot, 0, c.width - dot, c.height);
      g.fillStyle = '#ffd43b'; g.beginPath(); g.arc(dot / 2, c.height - dot / 2, dot / 2 - 1, 0, 2 * Math.PI); g.fill();
      g.strokeStyle = '#10141c'; g.lineWidth = 2; g.stroke();
      g.fillStyle = '#ffffff'; g.textBaseline = 'middle'; g.fillText(pr.text, dot + 10, c.height / 2 + 1);
      const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false }));
      const hgt = 0.021;
      sp.scale.set(hgt * c.width / c.height, hgt, 1);
      sp.center.set(dot / 2 / c.width, dot / 2 / c.height);  // the dot sits on the probed point
      sp.position.set(...pr.point); sp.renderOrder = 11;
      this.probeGroup.add(sp);
    }
  }

  // ---------------------------------------------------------------- overlays
  clearOverlay() {
    for (const c of [...this.overlayGroup.children]) { this.overlayGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
  }

  // payload: {positions, indices, values, per:'vertex'|'face', disp?}; opts: {range:[a,b], wire, flatColor, animate}
  // isosurface (vortex tubes: Q-criterion), coloured by another field; kept apart from the main overlay
  setIso(p, { range, opacity = 1 } = {}) {
    if (this.isoMesh) { this.scene.remove(this.isoMesh); this.isoMesh.geometry.dispose(); this.isoMesh.material.dispose(); this.isoMesh = null; }
    if (!p || !p.n_tris) return;
    const pos = b64(p.positions, Float32Array), val = b64(p.values, Float32Array);
    const [lo, hi] = range || [p.min, p.max], span = (hi - lo) || 1;
    const C = new Float32Array(val.length * 3);
    for (let i = 0; i < val.length; i++) {
      const c = Number.isFinite(val[i]) ? turbo(Math.min(1, Math.max(0, (val[i] - lo) / span))) : [0.7, 0.7, 0.7];
      C[i * 3] = c[0]; C[i * 3 + 1] = c[1]; C[i * 3 + 2] = c[2];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(C, 3));
    if (p.indices) g.setIndex(new THREE.BufferAttribute(b64(p.indices, Uint32Array), 1));
    g.computeVertexNormals();
    this.isoMesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, roughness: 0.45,
      metalness: 0.05, transparent: true, opacity, depthWrite: opacity >= 1 }));
    // drawn after the volume cloud (renderOrder 5, no depth test): the cloud would otherwise veil the vortices
    this.isoMesh.renderOrder = 6;
    this.scene.add(this.isoMesh);
  }

  // flat-coloured triangles of a surface payload: the geometry, its positions, the indices and the triangle count
  _triGeometry(p, range, reverse = false) {
    const pos = b64(p.positions, Float32Array), idx = b64(p.indices, Uint32Array), val = b64(p.values, Float32Array);
    const [lo, hi] = range || [p.min, p.max];
    const span = (hi - lo) || 1;
    const nt = idx.length / 3;
    const P = new Float32Array(nt * 9), C = new Float32Array(nt * 9);
    for (let t = 0; t < nt; t++) {
      for (let k = 0; k < 3; k++) {
        const v = idx[t * 3 + k];
        P[t * 9 + k * 3] = pos[v * 3]; P[t * 9 + k * 3 + 1] = pos[v * 3 + 1]; P[t * 9 + k * 3 + 2] = pos[v * 3 + 2];
        const s = p.per === 'vertex' ? val[v] : val[t];
        const c = Number.isFinite(s) ? turbo(reverse ? 1 - (s - lo) / span : (s - lo) / span) : [0.6, 0.6, 0.6];
        C[t * 9 + k * 3] = c[0]; C[t * 9 + k * 3 + 1] = c[1]; C[t * 9 + k * 3 + 2] = c[2];
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(P, 3));
    g.setAttribute('color', new THREE.BufferAttribute(C, 3));
    g.computeVertexNormals();
    return { g, P, idx, nt, val, per: p.per };
  }

  // ------------------------------------------------------------ volume rendering, arrows, particles
  _drop(name) {
    const o = this[name]; if (!o) return;
    this.scene.remove(o);
    o.traverse?.(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.uniforms?.uMap?.value?.dispose(); c.material?.uniforms?.uLut?.value?.dispose(); c.material?.dispose(); });
    this[name] = null;
  }

  // volume rendering: payload {dims, box, data (bytes, 0 = no data, 1..255 = value)}; the colour/opacity window
  // [lo, hi] (0-1 of the data range), density and number of ray steps are set with setVolumeWindow
  setVolume(p, win) {
    this._drop('volumeMesh');
    if (!p) return;
    const [nx, ny, nz] = p.dims, data = b64(p.data, Uint8Array);
    const tex = new THREE.Data3DTexture(data, nx, ny, nz);
    tex.format = THREE.RedFormat; tex.type = THREE.UnsignedByteType; tex.unpackAlignment = 1;
    tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.needsUpdate = true;
    const lut = new Uint8Array(256 * 4);
    for (let i = 0; i < 256; i++) { const c = turbo(i / 255); lut.set([c[0] * 255, c[1] * 255, c[2] * 255, 255], i * 4); }
    const lt = new THREE.DataTexture(lut, 256, 1, THREE.RGBAFormat); lt.minFilter = lt.magFilter = THREE.LinearFilter; lt.needsUpdate = true;
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3, side: THREE.BackSide, transparent: true, depthWrite: false, depthTest: false,
      uniforms: { uMap: { value: tex }, uLut: { value: lt }, uLo: { value: 0 }, uHi: { value: 1 }, uDens: { value: 4 }, uSteps: { value: 160 },
        uStyle: { value: 0 }, uDepth: { value: null }, uUseDepth: { value: 0 }, uRes: { value: new THREE.Vector2(1, 1) },
        uClipToWorld: { value: new THREE.Matrix4() }, uInvModel: { value: new THREE.Matrix4() } },
      vertexShader: `out vec3 vOrigin; out vec3 vDir;
        void main() { vOrigin = (inverse(modelMatrix) * vec4(cameraPosition, 1.0)).xyz; vDir = position - vOrigin;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `precision highp float; precision highp sampler3D;
        uniform sampler3D uMap; uniform sampler2D uLut, uDepth; uniform float uLo, uHi, uDens, uSteps, uStyle, uUseDepth;
        uniform vec2 uRes; uniform mat4 uClipToWorld, uInvModel;
        in vec3 vOrigin; in vec3 vDir; out vec4 outColor;
        vec2 hitBox(vec3 o, vec3 d) { vec3 inv = 1.0 / d; vec3 t0 = (vec3(-0.5) - o) * inv, t1 = (vec3(0.5) - o) * inv;
          vec3 a = min(t0, t1), b = max(t0, t1); return vec2(max(max(a.x, a.y), a.z), min(min(b.x, b.y), b.z)); }
        void main() {
          vec3 rd = normalize(vDir); vec2 h = hitBox(vOrigin, rd);
          if (h.x >= h.y) discard;
          h.x = max(h.x, 0.0);
          if (uUseDepth > 0.5) {   // stop at the nearest solid in front of the cloud (the model is not buried in it)
            vec2 uv = gl_FragCoord.xy / uRes; float d = texture(uDepth, uv).r;
            if (d < 1.0) {
              vec4 wp = uClipToWorld * vec4(uv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0); wp /= wp.w;
              vec3 po = (uInvModel * wp).xyz; h.y = min(h.y, dot(po - vOrigin, rd));
              if (h.x >= h.y) discard;
            }
          }
          float dt = (h.y - h.x) / uSteps; vec3 p = vOrigin + h.x * rd; vec4 acc = vec4(0.0);
          for (int i = 0; i < 512; i++) {
            if (float(i) >= uSteps || acc.a > 0.97) break;
            float v = texture(uMap, p + 0.5).r;
            if (v > 0.002) {
              float t = (v * 255.0 - 1.0) / 254.0;
              float w = clamp((t - uLo) / max(uHi - uLo, 1e-4), 0.0, 1.0);
              vec3 q = abs(p) * 2.0;   // fade out towards the faces of the box: no hard cut where the region ends
              float edge = (1.0 - smoothstep(0.72, 1.0, q.x)) * (1.0 - smoothstep(0.72, 1.0, q.y)) * (1.0 - smoothstep(0.72, 1.0, q.z));
              float ww = uStyle > 0.5 ? sqrt(w) : w;   // smoke: the thin outskirts of the slipstream show too
              float a = (1.0 - exp(-uDens * ww * ww * (3.0 - 2.0 * ww) * dt * 20.0)) * edge;
              vec3 c = uStyle > 0.5 ? mix(vec3(0.60, 0.76, 1.0), vec3(1.0, 0.97, 0.92), smoothstep(0.0, 1.0, w))   // smoke: pale blue to white
                                     : texture(uLut, vec2(w, 0.5)).rgb;
              acc.rgb += (1.0 - acc.a) * a * c; acc.a += (1.0 - acc.a) * a;
            }
            p += rd * dt;
          }
          if (acc.a < 0.003) discard;
          outColor = vec4(acc.rgb / acc.a, acc.a);
        }` });
    const b = p.box, m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    m.scale.set(b[3] - b[0], b[4] - b[1], b[5] - b[2]);
    m.position.set((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
    m.renderOrder = 5;
    this.volumeMesh = m; this.scene.add(m);
    this.setVolumeWindow(win);
  }
  setVolumeWindow({ lo = 0, hi = 1, density = 4, steps = 160, style = 'map' } = {}) {
    const u = this.volumeMesh?.material.uniforms; if (!u) return;
    u.uLo.value = lo; u.uHi.value = hi; u.uDens.value = density; u.uSteps.value = steps; u.uStyle.value = style === 'smoke' ? 1 : 0;
  }
  // a pass of the scene without the cloud into a depth texture: the cloud's rays end at the solids in front of it
  _volumeDepth(w, h, force = false) {
    const vm = this.volumeMesh, r = this.renderer;
    if (!vm || !vm.visible) return;
    // (half the resolution, and only when the camera moved, every quarter second otherwise, or when forced for a
    // video picture: the extra scene pass on every frame froze the page on a modest graphics card)
    const dpr = r.getPixelRatio(), W = Math.max(1, Math.floor(w * dpr)), H = Math.max(1, Math.floor(h * dpr));
    const dW = Math.max(1, W >> 1), dH = Math.max(1, H >> 1), now = performance.now();
    const key = this.camera.matrixWorld.elements.join(',') + '|' + W + 'x' + H;
    const u = vm.material.uniforms;
    if (force || key !== this._depthKey || now - (this._depthT || 0) > 250 || !this._depthRT) {
      if (!this._depthRT || this._depthRT.width !== dW || this._depthRT.height !== dH) {
        this._depthRT?.depthTexture?.dispose(); this._depthRT?.dispose();
        const rt = new THREE.WebGLRenderTarget(dW, dH);
        rt.depthTexture = new THREE.DepthTexture(dW, dH, THREE.UnsignedIntType);
        this._depthRT = rt;
      }
      vm.visible = false;
      const old = r.getRenderTarget();
      r.setRenderTarget(this._depthRT); r.clear(); r.render(this.scene, this.camera); r.setRenderTarget(old);
      vm.visible = true;
      this._depthKey = key; this._depthT = now;
    }
    u.uDepth.value = this._depthRT.depthTexture; u.uUseDepth.value = 1; u.uRes.value.set(W, H);
    vm.updateMatrixWorld();
    u.uInvModel.value.copy(vm.matrixWorld).invert();
    u.uClipToWorld.value.multiplyMatrices(this.camera.matrixWorld, this.camera.projectionMatrixInverse);
  }
  // turn the camera about the vertical axis through the target (a slow orbit in a video)
  orbitBy(rad) {
    const up = this.camera.up.clone().normalize(), v = this.camera.position.clone().sub(this.controls.target);
    v.applyAxisAngle(up, rad);
    this.camera.position.copy(this.controls.target).add(v);
    this.camera.lookAt(this.controls.target);
    this.camera.updateMatrixWorld();
  }
  cameraState() { return { p: this.camera.position.clone(), t: this.controls.target.clone() }; }
  setCameraState(s) { this.camera.position.copy(s.p); this.controls.target.copy(s.t); this.camera.lookAt(s.t); this.camera.updateMatrixWorld(); this.controls.update(); }

  // vector arrows: payload {positions, vectors (n x 3)}; length = lenScale * speed (m per unit of speed); colour = speed
  // over the range
  setArrows(p, { range, lenScale = 1, thick = 0.18, opacity = 1 } = {}) {
    this._drop('arrowMesh');
    if (!p || !p.n) return;
    const pos = b64(p.positions, Float32Array), vec = b64(p.vectors, Float32Array), n = p.n;
    const geo = new THREE.ConeGeometry(1, 1, 8); geo.translate(0, 0.5, 0);
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.5, transparent: opacity < 1, opacity });
    const im = new THREE.InstancedMesh(geo, mat, n);
    const [lo, hi] = range || [p.min, p.max], span = (hi - lo) || 1;
    const up = new THREE.Vector3(0, 1, 0), d = new THREE.Vector3(), q = new THREE.Quaternion(), M = new THREE.Matrix4(),
      sc = new THREE.Vector3(), c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      d.set(vec[i * 3], vec[i * 3 + 1], vec[i * 3 + 2]);
      const sp = d.length(), L = sp * lenScale;
      if (sp < 1e-12) { M.makeScale(0, 0, 0); im.setMatrixAt(i, M); continue; }
      q.setFromUnitVectors(up, d.multiplyScalar(1 / sp));
      sc.set(L * thick, L, L * thick);
      M.compose(new THREE.Vector3(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]), q, sc);
      im.setMatrixAt(i, M);
      const t = turbo((sp - lo) / span); c.setRGB(t[0], t[1], t[2]); im.setColorAt(i, c);
    }
    im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true;
    this.arrowMesh = im; this.scene.add(im);
  }

  // particles: payload {n, steps, paths (n x steps x 3, NaN after a particle ends), speed (n x steps)}. Each particle
  // runs along its path (phase shifted, then looping) at rate steps per second with a fading tail of `trail` segments.
  setParticles(p, { range, rate = 25, size = 3, trail = 10 } = {}) {
    this._drop('particleObj'); this._particles = null;
    if (!p || !p.n) return;
    const n = p.n, S = p.steps, P = b64(p.paths, Float32Array), V = b64(p.speed, Float32Array);
    const len = new Int32Array(n);
    for (let i = 0; i < n; i++) { let l = S; while (l > 0 && !Number.isFinite(P[(i * S + l - 1) * 3])) l--; len[i] = l; }
    const phase = Float32Array.from({ length: n }, (_, i) => Math.random() * Math.max(len[i], 1));
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    pg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const pts = new THREE.Points(pg, new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true }));
    pts.frustumCulled = false;
    const lg = new THREE.BufferGeometry();
    lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * trail * 6), 3));
    lg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * trail * 6), 3));
    const lines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true }));
    lines.frustumCulled = false;
    const grp = new THREE.Group(); grp.add(lines); grp.add(pts);
    this.particleObj = grp; this.scene.add(grp);
    this._particles = { n, S, P, V, len, phase, pg, lg, trail, rate, range: range || [p.min, p.max], t0: performance.now() };
  }
  setParticleStyle({ range, rate, size }) {
    const a = this._particles; if (!a) return;
    if (range) a.range = range;
    if (rate != null) a.rate = rate;
    if (size != null) this.particleObj.children[1].material.size = size;
  }
  _stepParticles() {
    const a = this._particles, tau = (performance.now() - a.t0) / 1000 * a.rate;
    const pp = a.pg.attributes.position.array, pc = a.pg.attributes.color.array,
      lp = a.lg.attributes.position.array, lc = a.lg.attributes.color.array, K = a.trail;
    const [lo, hi] = a.range, span = (hi - lo) || 1;
    for (let i = 0; i < a.n; i++) {
      const L = a.len[i];
      if (L < 3) { for (let k = 0; k < 3; k++) pp[i * 3 + k] = NaN; continue; }
      const s = Math.floor((tau + a.phase[i]) % L), base = i * a.S;
      const col = turbo((a.V[base + s] - lo) / span);
      for (let k = 0; k < 3; k++) { pp[i * 3 + k] = a.P[(base + s) * 3 + k]; pc[i * 3 + k] = col[k]; }
      for (let j = 0; j < K; j++) {
        const o = (i * K + j) * 6, ia = s - j, ib = s - j - 1, ok = ib >= 0, f = 1 - j / K;
        for (let k = 0; k < 3; k++) {
          lp[o + k] = a.P[(base + ia) * 3 + k]; lp[o + 3 + k] = a.P[(base + (ok ? ib : ia)) * 3 + k];
          lc[o + k] = lc[o + 3 + k] = col[k] * f;
        }
      }
    }
    a.pg.attributes.position.needsUpdate = a.pg.attributes.color.needsUpdate = true;
    a.lg.attributes.position.needsUpdate = a.lg.attributes.color.needsUpdate = true;
  }

  // contour planes: [{payload, range, opacity}] - cuts coloured by a field, each with its own opacity, drawn
  // together with the surfaces (vortices, walls); kept apart from the main overlay
  setContours(list) {
    if (!this.contourGroup) { this.contourGroup = new THREE.Group(); this.scene.add(this.contourGroup); }
    for (const c of [...this.contourGroup.children]) { this.contourGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
    for (const { payload, range, opacity = 1 } of list || []) {
      if (!payload || !payload.n_tris) continue;
      const { g } = this._triGeometry(payload, range);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide,
        transparent: opacity < 1, opacity, depthWrite: opacity >= 1 }));
      m.renderOrder = 2;
      this.contourGroup.add(m);
    }
  }

  setOverlay(p, { range, wire = false, opacity = 1, reverse = false, animate = true } = {}) {
    this.clearOverlay();
    this._anim = null;
    if (!p || !p.n_tris) return;
    const { g, P, idx, nt, val, per } = this._triGeometry(p, range, reverse);
    if (p.disp && animate) {
      // mode shape: oscillate the vertices; largest motion = 8 % of the model size (flat shading needs no normals)
      const dv = b64(p.disp, Float32Array), D = new Float32Array(nt * 9);
      for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
        const v = idx[t * 3 + k];
        for (let c = 0; c < 3; c++) D[t * 9 + k * 3 + c] = dv[v * 3 + c];
      }
      g.computeBoundingBox();
      const size = g.boundingBox.getSize(new THREE.Vector3()).length();
      this._anim = { attr: g.attributes.position, base: P.slice(), D, amp: 0.08 * size, t0: performance.now() };
    }
    const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.8, side: THREE.DoubleSide, flatShading: true,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1, transparent: opacity < 1, opacity }));
    mesh.userData.probe = { val, idx, per };  // (the values behind the colours, for probeAt)
    this.overlayGroup.add(mesh);
    if (wire) {
      const w = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0x10141c, wireframe: true, transparent: true, opacity: 0.35 }));
      this.overlayGroup.add(w);
    }
  }

  // boundary-condition glyphs: [{faces:[ids], color:'#rrggbb', label?, far?} + one of dir:[x,y,z] | normal:'in'|'out'
  // | symbol:'fixed'|'roller'|'plate'|'exchange'|'mass'|'marker'] or {point:[x,y,z], symbol:'source', ...}.
  // 'in' = into the region the face encloses (the part, or the air of an enclosure). An arrow that pushes on the
  // face ends at it (drawn outside); one that pulls or runs along the face starts at it. far: on the (hidden) air
  // volume of an external flow - sized to the whole volume, not to the part.
  setBcGlyphs(glyphs) {
    if (!this.bcGroup) { this.bcGroup = new THREE.Group(); this.scene.add(this.bcGroup); }
    for (const c of [...this.bcGroup.children]) { this.bcGroup.remove(c); c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); }
    if (!glyphs?.length || !this.meshes.length) return;
    const sizeAll = this.bounds(false).getSize(new THREE.Vector3()).length();
    const sizeVis = this.bounds(true).getSize(new THREE.Vector3()).length() || sizeAll;
    for (const g of glyphs) this._glyph(g, 0.09 * (g.far ? sizeAll : sizeVis) * (g.scale || 1), 1e-5 * sizeAll);
  }

  _label(text, color, pos) {
    const c = document.createElement('canvas'), g = c.getContext('2d'), fs = 26;
    g.font = `600 ${fs}px sans-serif`;
    c.width = Math.ceil(g.measureText(text).width) + 22; c.height = fs + 14;
    g.font = `600 ${fs}px sans-serif`;
    g.fillStyle = 'rgba(20,24,32,.82)'; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = color; g.fillRect(0, 0, 6, c.height);
    g.fillStyle = '#ffffff'; g.textBaseline = 'middle'; g.fillText(text, 13, c.height / 2 + 1);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false }));
    const h = 0.021;  // fraction of the view height
    sp.scale.set(h * c.width / c.height, h, 1);
    sp.center.set(0, 0);  // anchored at its lower-left corner, next to the glyph
    sp.position.copy(pos); sp.renderOrder = 10;
    this.bcGroup.add(sp);
  }

  _glyph(g, L, eps) {
    const shaft = new THREE.CylinderGeometry(0.03 * L, 0.03 * L, 0.7 * L, 8).translate(0, 0.35 * L, 0);
    const head = new THREE.ConeGeometry(0.1 * L, 0.3 * L, 14).translate(0, 0.85 * L, 0);
    const ray = new THREE.Raycaster();
    const solids = this.meshes.map(m => m.mesh);
    const up = new THREE.Vector3(0, 1, 0);
    // n flipped so that it points into the closed region behind the face (odd number of surfaces ahead)
    const inward = (p, n) => {
      ray.set(p.clone().addScaledVector(n, eps * 10), n);
      const hits = ray.intersectObjects(solids, false).filter(h => h.distance > eps * 20);
      const uniq = hits.filter((h, i) => i === 0 || h.distance - hits[i - 1].distance > eps * 20);
      return uniq.length % 2 ? n : n.clone().negate();
    };
    // darker than the face colour so glyphs stand out on their own face
    const col = new THREE.Color(g.color).multiplyScalar(0.6);
    const mat = new THREE.MeshStandardMaterial({ color: col, roughness: 0.5, emissive: col, emissiveIntensity: 0.3 });
    const put = (geo, pos, dir) => {
      const m = new THREE.Mesh(geo, mat);
      m.userData.far = !!g.far;  // (glyphs near the part are included when fitting the view)
      if (dir) m.quaternion.setFromUnitVectors(up, dir);
      m.position.copy(pos);
      this.bcGroup.add(m);
    };
    if (g.point && g.dir) {  // a body load (gravity) from the point, or the oncoming flow ending at it (end)
      const d = new THREE.Vector3(...g.dir).normalize(), p = new THREE.Vector3(...g.point).addScaledVector(d, g.end ? -L : 0);
      put(shaft, p, d); put(head, p, d);
      if (g.label) this._label(g.label, g.color, g.end ? p : p.clone().addScaledVector(d, L));
      return;
    }
    if (g.point) {  // heat source inside a body: a glowing sphere at its centre
      const p = new THREE.Vector3(...g.point);
      put(new THREE.SphereGeometry(0.12 * L, 20, 14), p);
      if (g.label) this._label(g.label, g.color, p.clone().addScaledVector(up, 0.15 * L));
      return;
    }
    const pts = this._samplePoints(g.faces, g.symbol === 'mass' ? 1 : g.faces.length > 40 ? 40 : 24);
    if (!pts.length) return;
    let anchor = null;
    for (const { p, n } of pts) {
      const nIn = inward(p, n), nOut = nIn.clone().negate();
      if (g.dir || g.normal) {
        const d = g.dir ? new THREE.Vector3(...g.dir).normalize() : (g.normal === 'out' ? nOut : nIn);
        const push = d.dot(nIn) > 0.2;
        const origin = push ? p.clone().addScaledVector(d, -L) : p.clone().addScaledVector(nIn, -eps * 50);
        put(shaft, origin, d); put(head, origin, d);
        anchor = anchor || (push ? origin : origin.clone().addScaledVector(d, L));
        continue;
      }
      const s = 0.17 * L;
      if (g.symbol === 'fixed') put(new THREE.BoxGeometry(s, s, s), p.clone().addScaledVector(nOut, s / 2), nOut);
      else if (g.symbol === 'roller') put(new THREE.SphereGeometry(s / 2, 14, 10), p.clone().addScaledVector(nOut, s / 2));
      else if (g.symbol === 'plate') put(new THREE.CylinderGeometry(s * 0.9, s * 0.9, s * 0.12, 18), p.clone().addScaledVector(nOut, s * 0.06), nOut);
      else if (g.symbol === 'mass') put(new THREE.SphereGeometry(1.6 * s, 20, 14), p.clone().addScaledVector(nOut, 1.6 * s), nOut);
      else if (g.symbol === 'exchange') {  // short outward arrows: heat exchanged with the surroundings
        const o = p.clone().addScaledVector(nIn, -eps * 50), k = 0.55;
        const sh = new THREE.CylinderGeometry(0.03 * L, 0.03 * L, 0.7 * k * L, 8).translate(0, 0.35 * k * L, 0);
        const hd = new THREE.ConeGeometry(0.09 * L, 0.25 * L, 12).translate(0, (0.7 * k + 0.12) * L, 0);
        put(sh, o, nOut); put(hd, o, nOut);
      } else put(new THREE.SphereGeometry(s * 0.35, 10, 8), p.clone().addScaledVector(nOut, s * 0.35));  // marker
      anchor = anchor || p.clone().addScaledVector(nOut, 1.2 * s);
    }
    if (g.label && anchor) this._label(g.label, g.color, anchor);
  }

  // points spread evenly over the faces (farthest-point sampling of triangle centres), with the normal there;
  // each face gets a share by area and at least one point while there are few faces
  _samplePoints(faceIds, budget) {
    const per = [];
    let total = 0;
    for (const fid of faceIds) {
      const f = this.faceById.get(fid);
      if (!f) continue;
      const v = f.mesh.vertices, t = f.mesh.triangles, list = [];
      let a = 0;
      for (let i = 0; i < t.length; i += 3) {
        const A = new THREE.Vector3(v[t[i] * 3], v[t[i] * 3 + 1], v[t[i] * 3 + 2]);
        const B = new THREE.Vector3(v[t[i + 1] * 3], v[t[i + 1] * 3 + 1], v[t[i + 1] * 3 + 2]);
        const C = new THREE.Vector3(v[t[i + 2] * 3], v[t[i + 2] * 3 + 1], v[t[i + 2] * 3 + 2]);
        const n = new THREE.Vector3().crossVectors(B.clone().sub(A), C.clone().sub(A));
        const ar = n.length() / 2;
        if (ar <= 0) continue;
        a += ar;
        list.push({ p: A.add(B).add(C).divideScalar(3), n: n.normalize(), a: ar });
      }
      if (list.length) { per.push({ list, a }); total += a; }
    }
    const spread = (list, k) => {
      if (list.length > 4000) list = list.filter((_, i) => i % Math.ceil(list.length / 4000) === 0);
      // start next to the area-weighted centre, then always take the point farthest from those chosen
      const c = list.reduce((s, x) => s.addScaledVector(x.p, x.a), new THREE.Vector3())
        .divideScalar(list.reduce((s, x) => s + x.a, 0));
      let first = 0;
      list.forEach((x, i) => { if (x.p.distanceToSquared(c) < list[first].p.distanceToSquared(c)) first = i; });
      const out = [list[first]], dmin = list.map(x => x.p.distanceToSquared(list[first].p));
      while (out.length < Math.min(k, list.length)) {
        let best = 0;
        for (let i = 1; i < list.length; i++) if (dmin[i] > dmin[best]) best = i;
        if (dmin[best] <= 0) break;
        out.push(list[best]);
        for (let i = 0; i < list.length; i++) dmin[i] = Math.min(dmin[i], list[i].p.distanceToSquared(list[best].p));
      }
      return out;
    };
    if (per.length <= budget)
      return per.flatMap(({ list, a }) => spread(list, Math.min(8, Math.max(1, Math.round(budget * a / total)))));
    return spread(per.flatMap(x => x.list), budget);  // many faces: spread a fixed number over all of them
  }

  // air-volume editor: outline of the automatic air volume with drag handles.
  // spec (metres, as simwb/domain.py): {shape:'box', min, max} | {shape:'c_domain', center, flow_axis, flow_sign,
  // span_axis, third_axis, radius, downstream, span_min, span_max} | {shape:'cylinder', ..., radius, upstream,
  // downstream}. onChange(key, value) is called while a handle is dragged (key: 'min.0', 'radius', ...).
  setAirEditor(spec, onChange, dirty = false) {
    if (!this.airGroup) { this.airGroup = new THREE.Group(); this.scene.add(this.airGroup); this._bindAirDrag(); }
    for (const c of [...this.airGroup.children]) { this.airGroup.remove(c); c.geometry?.dispose(); c.material?.dispose(); }
    this.airHandles = [];
    this.airSpec = spec; this.airOnChange = onChange;
    if (!spec) return;
    const V = (...a) => new THREE.Vector3(...a);
    const E = [V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)];
    const pts = [];  // line segments
    const seg = (a, b) => pts.push(a.x, a.y, a.z, b.x, b.y, b.z);
    const poly = list => list.forEach((p, i) => i && seg(list[i - 1], p));
    const handles = [];  // {p, axis (unit), key, sign}
    let diag;
    if (spec.shape === 'box') {
      const lo = V(...spec.min), hi = V(...spec.max);
      diag = hi.distanceTo(lo);
      const c = lo.clone().add(hi).multiplyScalar(0.5);
      for (let i = 0; i < 8; i++) {
        const p = V(i & 1 ? hi.x : lo.x, i & 2 ? hi.y : lo.y, i & 4 ? hi.z : lo.z);
        for (const b of [1, 2, 4]) if (!(i & b)) seg(p, V(i & 1 || b === 1 ? hi.x : lo.x, i & 2 || b === 2 ? hi.y : lo.y, i & 4 || b === 4 ? hi.z : lo.z));
      }
      for (let a = 0; a < 3; a++) for (const [key, v] of [['min', lo], ['max', hi]]) {
        const p = c.clone(); p.setComponent(a, v.getComponent(a));
        handles.push({ p, axis: E[a], key: `${key}.${a}`, value: v.getComponent(a), sign: 1 });
      }
    } else {
      const c = V(...spec.center), a = spec.flow_axis, s = spec.flow_sign, R = spec.radius;
      const fa = E[a].clone().multiplyScalar(s);
      if (spec.shape === 'c_domain') {
        const b = spec.span_axis, e = spec.third_axis, D = spec.downstream;
        const outline = sv => {  // the C at one span end
          const o = c.clone(); o.setComponent(b, sv);
          const list = [];
          for (let i = 0; i <= 32; i++) {  // half circle upstream, from +e round to -e
            const t = Math.PI / 2 + Math.PI * i / 32;
            list.push(o.clone().addScaledVector(fa, R * Math.cos(t)).addScaledVector(E[e], R * Math.sin(t)));
          }
          list.push(o.clone().addScaledVector(fa, D).addScaledVector(E[e], -R), o.clone().addScaledVector(fa, D).addScaledVector(E[e], R), list[0]);
          return list;
        };
        const A = outline(spec.span_min), B = outline(spec.span_max);
        poly(A); poly(B);
        for (const i of [0, 8, 16, 24, 32, 33, 34]) seg(A[i], B[i]);
        diag = Math.hypot(R + D, 2 * R, spec.span_max - spec.span_min);
        const mid = (spec.span_min + spec.span_max) / 2, cm = c.clone(); cm.setComponent(b, mid);
        handles.push({ p: cm.clone().addScaledVector(fa, -R), axis: fa.clone().negate(), key: 'radius', value: R, sign: 1 });
        handles.push({ p: cm.clone().addScaledVector(fa, D), axis: fa.clone(), key: 'downstream', value: D, sign: 1 });
        for (const [key, v] of [['span_min', spec.span_min], ['span_max', spec.span_max]]) {
          const p = c.clone().addScaledVector(fa, (D - R) / 2); p.setComponent(b, v);
          handles.push({ p, axis: E[b], key, value: v, sign: 1 });
        }
      } else {
        const U = spec.upstream, D = spec.downstream;
        const o = [0, 1, 2].filter(i => i !== a);
        const ring = x => Array.from({ length: 49 }, (_, i) => c.clone().addScaledVector(fa, x)
          .addScaledVector(E[o[0]], R * Math.cos(2 * Math.PI * i / 48)).addScaledVector(E[o[1]], R * Math.sin(2 * Math.PI * i / 48)));
        const A = ring(-U), B = ring(D);
        poly(A); poly(B);
        for (const i of [0, 12, 24, 36]) seg(A[i], B[i]);
        diag = Math.hypot(U + D, 2 * R);
        handles.push({ p: c.clone().addScaledVector(fa, -U), axis: fa.clone().negate(), key: 'upstream', value: U, sign: 1 });
        handles.push({ p: c.clone().addScaledVector(fa, D), axis: fa.clone(), key: 'downstream', value: D, sign: 1 });
        handles.push({ p: c.clone().addScaledVector(fa, (D - U) / 2).addScaledVector(E[o[1]], R), axis: E[o[1]], key: 'radius', value: R, sign: 1 });
      }
      // flow direction arrow upstream of the volume
      const L = 0.12 * diag, start = c.clone().addScaledVector(fa, -(spec.shape === 'cylinder' ? spec.upstream : R) - 1.3 * L);
      this.airGroup.add(new THREE.ArrowHelper(fa, start, L, 0x2f6fe0, 0.3 * L, 0.15 * L));
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    this.airGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: dirty ? 0xff8a00 : 0x5b8cff, transparent: true, opacity: 0.9 })));
    const r = 0.012 * diag;
    for (const h of handles) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 12), new THREE.MeshStandardMaterial({ color: 0xff8a00, emissive: 0x803000 }));
      m.position.copy(h.p); m.userData.handle = h;
      this.airGroup.add(m);
      this.airHandles.push(m);
    }
  }

  // dragging a handle moves it along its axis (closest point between the mouse ray and that axis)
  _bindAirDrag() {
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const host = this.canvas.parentElement;
    let drag = null;
    const castFrom = ev => {
      const rect = this.canvas.getBoundingClientRect();
      ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, this.camera);
    };
    const along = h => {  // parameter along the handle axis of the point closest to the mouse ray
      const d = ray.ray.direction, u = h.axis, w0 = ray.ray.origin.clone().sub(h.p);
      const b = d.dot(u), den = 1 - b * b;
      return den < 1e-6 ? null : (u.dot(w0) - b * d.dot(w0)) / den;
    };
    const hit = ev => {
      if (!this.airHandles?.length || !this.airGroup.visible) return null;
      castFrom(ev);
      return ray.intersectObjects(this.airHandles, false)[0]?.object || null;
    };
    host.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || ev.target !== this.canvas) return;
      const m = hit(ev);
      if (!m) return;
      ev.stopPropagation(); ev.preventDefault();
      const h = m.userData.handle;
      drag = { h, m, t0: along(h) };
      this.canvas.setPointerCapture?.(ev.pointerId);
    }, true);
    host.addEventListener('pointermove', ev => {
      if (!drag) { if (ev.target === this.canvas) this.canvas.style.cursor = hit(ev) ? 'grab' : ''; return; }
      ev.stopPropagation();
      castFrom(ev);
      const t = along(drag.h);
      if (t == null || drag.t0 == null) return;
      const v = drag.h.value + (t - drag.t0);
      drag.m.position.copy(drag.h.p).addScaledVector(drag.h.axis, t - drag.t0);
      this.airOnChange && this.airOnChange(drag.h.key, v, false);
    }, true);
    const end = ev => {
      if (!drag) return;
      ev.stopPropagation();
      castFrom(ev);
      const t = along(drag.h);
      const d = drag; drag = null;
      this.canvas.style.cursor = '';
      if (t != null && d.t0 != null) this.airOnChange && this.airOnChange(d.h.key, d.h.value + (t - d.t0), true);
    };
    host.addEventListener('pointerup', end, true);
    host.addEventListener('pointercancel', end, true);
  }

  // mesh edges of the moving-zone boundaries (shown with the mesh)
  // refinement boxes: orange wire boxes with their name
  setRegions(regions, show) {
    if (!this.regionGroup) { this.regionGroup = new THREE.Group(); this.scene.add(this.regionGroup); }
    for (const c of [...this.regionGroup.children]) { this.regionGroup.remove(c); c.traverse?.(o => { o.geometry?.dispose(); o.material?.map?.dispose(); }); }
    if (!show) return;
    for (const r of regions || []) {
      if (r.shape === 'cylinder') {
        const a = new THREE.Vector3(...r.start), b = new THREE.Vector3(...r.end), d = b.clone().sub(a);
        const cyl = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.CylinderGeometry(r.radius, r.radius, d.length(), 48, 1, false), 30),
          new THREE.LineBasicMaterial({ color: 0xff9f1c }));
        cyl.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
        cyl.position.copy(a.clone().add(b).multiplyScalar(0.5));
        this.regionGroup.add(cyl);
        if (r.inner_radius) {  // ring: the inner wall too
          const inner = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.CylinderGeometry(r.inner_radius, r.inner_radius, d.length(), 48, 1, false), 30),
            new THREE.LineBasicMaterial({ color: 0xff9f1c, transparent: true, opacity: 0.5 }));
          inner.quaternion.copy(cyl.quaternion); inner.position.copy(cyl.position);
          this.regionGroup.add(inner);
        }
      } else {
        const sz = [0, 1, 2].map(k => r.max[k] - r.min[k]);
        const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...sz)),
          new THREE.LineBasicMaterial({ color: 0xff9f1c }));
        box.position.set(...[0, 1, 2].map(k => (r.min[k] + r.max[k]) / 2));
        this.regionGroup.add(box);
      }
      if (!this.bcGroup) { this.bcGroup = new THREE.Group(); this.scene.add(this.bcGroup); }
      this._label(`${r.name}: ${+(r.size * 1000).toPrecision(3)} mm cells`, '#ff9f1c', new THREE.Vector3(...r.max));
      this.regionGroup.add(this.bcGroup.children[this.bcGroup.children.length - 1]);  // (moves it out of the BC glyphs)
    }
  }

  setZoneWire(flat) {
    if (this.zoneWire) { this.scene.remove(this.zoneWire); this.zoneWire.geometry.dispose(); this.zoneWire = null; }
    if (!flat || !flat.length) return;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(flat, 3));
    this.zoneWire = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xb15cff, transparent: true, opacity: 0.9 }));
    this.scene.add(this.zoneWire);
  }

  // rotating zones: translucent cylinder + an arc arrow showing the sense of rotation (right-hand rule, rpm sign)
  setMovingZones(zones, visible = true) {
    if (!this.zoneGroup) { this.zoneGroup = new THREE.Group(); this.scene.add(this.zoneGroup); }
    for (const c of [...this.zoneGroup.children]) { this.zoneGroup.remove(c); c.traverse?.(o => o.geometry?.dispose()); }
    if (!visible) return;
    for (const z of zones || []) {
      const ax = new THREE.Vector3(...z.axisVec).normalize();
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), ax);
      const col = z.method === 'sliding' ? 0xb15cff : 0x8f7bff;
      const cyl = new THREE.Mesh(new THREE.CylinderGeometry(z.radius, z.radius, z.length, 64, 1, false),
        new THREE.MeshStandardMaterial({ color: col, transparent: true, opacity: 0.13, depthWrite: false, side: THREE.DoubleSide }));
      cyl.quaternion.copy(q); cyl.position.set(...z.center);
      this.zoneGroup.add(cyl);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.CylinderGeometry(z.radius, z.radius, z.length, 64, 1, false), 30),
        new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.8 }));
      edges.quaternion.copy(q); edges.position.set(...z.center);
      this.zoneGroup.add(edges);
      // rotation arrow: 3/4 of a circle just outside the cylinder, at its upper end, with a cone at the tip
      const R = z.radius * 1.12, t = Math.max(z.radius * 0.025, 1e-4), sgn = z.rpm >= 0 ? 1 : -1;
      const arc = new THREE.Mesh(new THREE.TorusGeometry(R, t, 8, 64, Math.PI * 1.5), new THREE.MeshBasicMaterial({ color: col }));
      const cone = new THREE.Mesh(new THREE.ConeGeometry(t * 4, t * 10, 16), new THREE.MeshBasicMaterial({ color: col }));
      const g = new THREE.Group();
      g.add(arc);
      // torus lies in the XY plane from angle 0 to 1.5 pi; its end tangent is along -x at (0,-R) for +rotation
      cone.position.set(0, -R, 0); cone.rotation.z = sgn > 0 ? Math.PI / 2 : -Math.PI / 2;
      if (sgn < 0) { cone.position.set(R, 0, 0); cone.rotation.z = Math.PI; }
      g.add(cone);
      // torus axis is z: turn it onto the zone axis, place it at the upper end of the cylinder
      g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), ax);
      g.position.set(...z.center).addScaledVector(ax, z.length / 2);
      this.zoneGroup.add(g);
    }
  }

  setRotors(rotors, visible = true) {
    for (const c of [...this.rotorGroup.children]) { this.rotorGroup.remove(c); c.geometry?.dispose(); }
    if (!visible) return;
    for (const r of rotors || []) {
      const g = new THREE.CylinderGeometry(r.radius, r.radius, Math.max(r.thickness, r.radius * 0.02), 48, 1, false);
      const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xff8a00, transparent: true, opacity: 0.18, depthWrite: false, side: THREE.DoubleSide }));
      m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(...r.axis).normalize());
      m.position.set(...r.center);
      this.rotorGroup.add(m);
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(...r.axis).normalize(), new THREE.Vector3(...r.center),
        r.radius * 0.8, 0xff8a00, r.radius * 0.2, r.radius * 0.12);
      this.rotorGroup.add(arrow);
    }
  }
}
