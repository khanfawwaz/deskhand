/* The Deskhand orb: a glossy green drop that moves like water. Its centre stays put. When the cursor comes near, the part of
   the edge closest to it stretches toward the cursor like a drop drawn by a magnet, and springs back with a ripple when the
   cursor leaves. Press and hold to grab it: it stretches much further, into a thinning neck. Pull far enough and it snaps
   (and, quietly, flips the light/dark theme: a hidden easter egg, no prompt). Pure canvas, no libraries. */
(() => {
  'use strict';
  const cv = document.getElementById('orb');
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const SIZE = 460;            // css px of the canvas (room for the glow and a very long stretch); the page lays it out as 260
  const R = 58;                // radius of the drop
  const N = 150;               // points around the edge
  const ZONE = R * 2.15;       // the soft halo: the gentle pull starts when the cursor is inside it
  const REACH = 160;           // the longest a held drop can stretch
  const SNAP = 134;            // the stretch you can actually SEE (px beyond the edge) at which the drop lets go
  const h = new Float32Array(N), v = new Float32Array(N), tgt = new Float32Array(N);
  let dpr = 1, t = 0, ox = 0, oy = 0, tx = 0, ty = 0, wobble = 0;
  let cursor = null;           // the cursor relative to the middle of the drop, or null when it is not on the page
  let held = false, armed = true, loose = false;   // held: button down on the drop; armed: may snap; loose: just snapped, let go

  function resize() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = cv.height = Math.round(SIZE * dpr);
    cv.style.width = cv.style.height = SIZE + 'px';
  }

  const angDiff = (i, ang) => Math.abs((((i / N) * Math.PI * 2 - ang) % (Math.PI * 2) + Math.PI * 3) % (Math.PI * 2) - Math.PI);

  function relative(e) {
    const r = cv.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    return { dx, dy, dist: Math.hypot(dx, dy) };
  }

  function onMove(e) {
    cursor = relative(e);
    tx = Math.max(-1, Math.min(1, cursor.dx / (R * 2.4))); ty = Math.max(-1, Math.min(1, cursor.dy / (R * 2.4)));
    const speed = Math.min(40, Math.hypot(e.movementX || 0, e.movementY || 0));
    wobble = Math.min(1, wobble + speed * 0.008);
    if (!held && cursor.dist < R * 0.95) {                                    // poking inside: a dent that ripples
      const ang = Math.atan2(cursor.dy, cursor.dx);
      for (let i = 0; i < N; i++) v[i] -= Math.exp(-(angDiff(i, ang) ** 2) / 0.3) * (0.4 + speed * 0.05);
    }
  }

  // where the edge wants to be: the closest point stretches toward the cursor, the rest of the drop stays round
  function aim() {
    tgt.fill(0);
    if (!cursor || loose) return;
    const gap = cursor.dist - R;                                                // how far the cursor is from the edge
    let peak, sharp;
    if (held) {                                                                 // grabbed: the tip follows the cursor, a thinning neck
      if (cursor.dist < R * 0.6) return;
      peak = Math.min(REACH, Math.max(0, gap) * 1.15 + 6);
      sharp = 0.26 - 0.17 * Math.min(1, peak / REACH);                         // wide at first, a thin neck when stretched far
      let seen = 0;
      for (let i = 0; i < N; i++) if (h[i] > seen) seen = h[i];
      if (armed && seen >= SNAP) snap();                                       // only once the drop has really stretched that far
    } else {                                                                    // hovering: a stretchier pull inside the halo
      if (cursor.dist >= ZONE || cursor.dist < R * 0.95) return;
      const near = 1 - Math.max(0, gap) / (ZONE - R);
      peak = Math.min(46, Math.max(0, gap) * 1.0 + 8) * Math.sqrt(Math.max(0, near));
      sharp = 0.24;
    }
    const ang = Math.atan2(cursor.dy, cursor.dx);
    for (let i = 0; i < N; i++) tgt[i] = peak * Math.exp(-(angDiff(i, ang) ** 2) / sharp);
  }

  // pulled too far: the drop lets go with a big wobble, and the page quietly switches theme
  function snap() {
    armed = false; loose = true;
    for (let i = 0; i < N; i++) v[i] -= 2.5 + Math.random() * 1.5;
    wobble = 1;
    window.dispatchEvent(new CustomEvent('deskhand:toggle-theme'));
  }

  function step() {
    t += 0.016;
    aim();
    const k = held ? 0.10 : 0.09;                                               // a softer spring: it lags the hand and overshoots a little
    const damp = held ? 0.90 : 0.88;
    for (let i = 0; i < N; i++) {
      const l = h[(i + N - 1) % N], r = h[(i + 1) % N];
      v[i] += (l + r - 2 * h[i]) * 0.26;                                        // neighbours pull each other: ripples travel round
      v[i] += (tgt[i] - h[i]) * k;                                              // spring toward where the cursor wants the edge
      v[i] += Math.sin(t * 1.4 + i * 0.15) * 0.012 + Math.sin(t * 0.8 - i * 0.25) * 0.01;   // idle breathing
      v[i] *= damp;
    }
    for (let i = 0; i < N; i++) h[i] = Math.max(-24, Math.min(REACH + 12, h[i] + v[i]));
    ox += (tx * 12 - ox) * 0.08; oy += (ty * 12 - oy) * 0.08;                  // the highlight follows the cursor
    wobble *= 0.96;
  }

  function edge(cx, cy) {
    const pts = [];
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2, rr = R + h[i];
      pts.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
    }
    ctx.beginPath();
    for (let i = 0; i < N; i++) {                                              // smooth curve through the midpoints
      const p = pts[i], q = pts[(i + 1) % N], mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
      if (i === 0) ctx.moveTo((pts[N - 1][0] + p[0]) / 2, (pts[N - 1][1] + p[1]) / 2);
      ctx.quadraticCurveTo(p[0], p[1], mx, my);
    }
    ctx.closePath();
  }

  function draw() {
    const c = SIZE / 2, cx = c, cy = c;                                        // the centre never moves
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);

    // on white the same halo is almost invisible (on dark it glows), so the light theme gets a stronger halo and a light rim
    const attr = document.documentElement.getAttribute('data-theme');
    const light = attr ? attr === 'light' : !(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);

    // soft halo
    const glow = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, ZONE);
    glow.addColorStop(0, light ? 'rgba(110,217,135,0.42)' : 'rgba(110,217,135,0.16)');
    glow.addColorStop(light ? 0.45 : 0.5, light ? 'rgba(110,217,135,0.16)' : 'rgba(110,217,135,0.06)');
    glow.addColorStop(1, 'rgba(110,217,135,0)');
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(cx, cy, ZONE, 0, 7); ctx.fill();

    // body
    edge(cx, cy);
    const body = ctx.createRadialGradient(cx - R * 0.3 + ox, cy - R * 0.35 + oy, R * 0.1, cx, cy, R * 1.15);
    body.addColorStop(0, '#e9fff0'); body.addColorStop(0.28, '#6ED987'); body.addColorStop(0.68, '#1BC237'); body.addColorStop(1, '#0b7a26');
    ctx.save(); ctx.shadowColor = light ? 'rgba(27,194,55,0.55)' : 'rgba(27,194,55,0.30)'; ctx.shadowBlur = light ? 34 : 22; ctx.fillStyle = body; ctx.fill(); ctx.restore();
    if (light) {                                                                // the light outline
      edge(cx, cy);
      ctx.save(); ctx.lineWidth = 5; ctx.strokeStyle = 'rgba(190,245,205,0.85)'; ctx.shadowColor = 'rgba(110,217,135,0.9)'; ctx.shadowBlur = 14; ctx.stroke(); ctx.restore();
    }

    // liquid swirls inside, clipped to the drop
    ctx.save(); edge(cx, cy); ctx.clip();
    for (let k = 0; k < 3; k++) {
      const a = t * (0.5 + k * 0.23) + k * 2.1, rr = R * (0.35 + 0.12 * k);
      const sx = cx + Math.cos(a) * rr + ox * 0.5, sy = cy + Math.sin(a * 1.3) * rr * 0.8 + oy * 0.5;
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, R * (0.55 + wobble * 0.3));
      const tint = [[120, 235, 215], [255, 255, 255], [225, 250, 140]][k];
      g.addColorStop(0, 'rgba(' + tint.join(',') + ',' + (k === 1 ? 0.5 : 0.55) + ')'); g.addColorStop(1, 'rgba(' + tint.join(',') + ',0)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, SIZE, SIZE);
    }
    const dark = ctx.createRadialGradient(cx + R * 0.35 - ox * 0.4, cy + R * 0.45 - oy * 0.4, R * 0.2, cx, cy, R * 1.1);
    dark.addColorStop(0, 'rgba(6,90,28,0)'); dark.addColorStop(1, 'rgba(6,90,28,0.45)');
    ctx.fillStyle = dark; ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.restore();

    // specular highlight
    ctx.save(); ctx.translate(cx - R * 0.32 + ox * 0.9, cy - R * 0.42 + oy * 0.9); ctx.rotate(-0.6);
    const sp = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 0.34);
    sp.addColorStop(0, 'rgba(255,255,255,0.95)'); sp.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.scale(1.5, 0.8); ctx.fillStyle = sp; ctx.beginPath(); ctx.arc(0, 0, R * 0.34, 0, 7); ctx.fill(); ctx.restore();
  }

  function frame() {
    requestAnimationFrame(frame);
    if (document.hidden || !cv.offsetParent) return;                          // nothing to draw while it is not on screen
    step(); draw();
  }

  resize();
  window.addEventListener('resize', resize);
  if (reduce) { draw(); return; }                                              // a still drop for people who prefer less motion
  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('pointerdown', (e) => {                              // press near the drop to grab it
    const p = relative(e);
    if (p.dist < ZONE) {
      cursor = p; held = true; armed = true; loose = false;
      document.documentElement.classList.add('orb-grab');                      // no text selection while pulling the drop
      try { window.getSelection().removeAllRanges(); } catch (err) { /* ignore */ }
      for (let i = 0; i < N; i++) v[i] -= 0.8 + Math.random() * 0.4;           // a little squish as it is grabbed
    }
  }, { passive: true });
  // pressing on the drop must never start selecting the text around it (a mouse press is cancelled before it can)
  window.addEventListener('mousedown', (e) => { if (e.button === 0 && relative(e).dist < ZONE) e.preventDefault(); });
  window.addEventListener('selectstart', (e) => { if (held) e.preventDefault(); });
  const release = () => { document.documentElement.classList.remove('orb-grab'); if (held) { held = false; loose = false; armed = true; wobble = 1; } };
  window.addEventListener('pointerup', release, { passive: true });
  window.addEventListener('pointercancel', release, { passive: true });
  const rest = () => { cursor = null; tx = 0; ty = 0; release(); };            // the pointer left the page: the drop settles
  document.documentElement.addEventListener('pointerleave', rest);
  window.addEventListener('blur', rest);
  frame();
})();
