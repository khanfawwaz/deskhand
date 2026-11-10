/**
 * The Deskhand orb: a glossy green drop that moves like water. Its centre stays put. When the cursor comes near, the part
 * of the edge closest to it stretches toward the cursor and springs back with a ripple. Press and hold to grab it: it
 * stretches much further, into a thinning neck. Pull far enough and it lets go, and the page quietly switches between
 * light and dark (a hidden extra, no prompt). Pure canvas, no libraries. The physics live in orbSim.ts.
 */
import { useEffect, useRef } from 'react';
import { effectiveTheme, useTheme } from '../../state/theme';
import styles from './Orb.module.css';
import { N, OrbSim, pointAt, R, SIZE, ZONE } from './orbSim';

const GRAB_CLASS = 'orb-grab'; // on <html> while the drop is being pulled: nothing on the page can be selected

export function Orb() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext?.('2d');
    if (!cv || !ctx) return; // no canvas (very old browser, or a test environment): show nothing
    const sim = new OrbSim();
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let dpr = 1;
    let raf = 0;

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      cv.width = cv.height = Math.round(SIZE * dpr);
    };
    const relative = (e: MouseEvent) => {
      const r = cv.getBoundingClientRect();
      return pointAt(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2));
    };
    const paint = () => draw(ctx, sim, dpr, effectiveTheme() === 'light');

    resize();
    window.addEventListener('resize', resize);
    if (reduce) {
      // a still drop for people who prefer less motion (redrawn when the theme changes)
      paint();
      const unsubscribe = useTheme.subscribe(paint);
      const repaint = () => {
        resize();
        paint();
      };
      window.addEventListener('resize', repaint);
      return () => {
        unsubscribe();
        window.removeEventListener('resize', resize);
        window.removeEventListener('resize', repaint);
      };
    }

    const onMove = (e: PointerEvent) => sim.move(relative(e), Math.hypot(e.movementX || 0, e.movementY || 0));
    const onDown = (e: PointerEvent) => {
      if (!sim.press(relative(e))) return;
      document.documentElement.classList.add(GRAB_CLASS);
      try {
        window.getSelection()?.removeAllRanges();
      } catch {
        /* nothing selected */
      }
    };
    // pressing on the drop must never start selecting the text around it
    const onMouseDown = (e: MouseEvent) => {
      if (e.button === 0 && relative(e).dist < ZONE) e.preventDefault();
    };
    const onSelectStart = (e: Event) => {
      if (sim.held) e.preventDefault();
    };
    const release = () => {
      document.documentElement.classList.remove(GRAB_CLASS);
      sim.release();
    };
    const rest = () => {
      document.documentElement.classList.remove(GRAB_CLASS);
      sim.rest();
    };
    const frame = () => {
      raf = requestAnimationFrame(frame);
      if (document.hidden || !cv.offsetParent) return; // nothing to draw while it is not on screen
      if (sim.step()) useTheme.getState().toggle(); // pulled far enough: flip light/dark, no prompt
      paint();
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    window.addEventListener('pointerdown', onDown, { passive: true });
    window.addEventListener('mousedown', onMouseDown);
    window.addEventListener('selectstart', onSelectStart);
    window.addEventListener('pointerup', release, { passive: true });
    window.addEventListener('pointercancel', release, { passive: true });
    document.documentElement.addEventListener('pointerleave', rest);
    window.addEventListener('blur', rest);
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      document.documentElement.classList.remove(GRAB_CLASS);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('selectstart', onSelectStart);
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', release);
      document.documentElement.removeEventListener('pointerleave', rest);
      window.removeEventListener('blur', rest);
    };
  }, []);

  return <canvas ref={ref} className={styles.orb} width={SIZE} height={SIZE} aria-hidden="true" data-testid="orb" />;
}

function edgePath(ctx: CanvasRenderingContext2D, sim: OrbSim, cx: number, cy: number): void {
  const xs = new Float32Array(N);
  const ys = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const a = (i / N) * Math.PI * 2;
    const rr = R + sim.h[i]!;
    xs[i] = cx + Math.cos(a) * rr;
    ys[i] = cy + Math.sin(a) * rr;
  }
  ctx.beginPath();
  ctx.moveTo((xs[N - 1]! + xs[0]!) / 2, (ys[N - 1]! + ys[0]!) / 2);
  for (let i = 0; i < N; i++) {
    // a smooth curve through the midpoints
    const j = (i + 1) % N;
    ctx.quadraticCurveTo(xs[i]!, ys[i]!, (xs[i]! + xs[j]!) / 2, (ys[i]! + ys[j]!) / 2);
  }
  ctx.closePath();
}

const TINTS = [
  [120, 235, 215],
  [255, 255, 255],
  [225, 250, 140],
] as const;

function draw(ctx: CanvasRenderingContext2D, sim: OrbSim, dpr: number, light: boolean): void {
  const cx = SIZE / 2;
  const cy = SIZE / 2; // the centre never moves
  const { ox, oy, t, wobble } = sim;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, SIZE, SIZE);

  // soft halo. On white the same halo is almost invisible (on dark it glows), so light gets a stronger one and a rim
  const glow = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, ZONE);
  glow.addColorStop(0, light ? 'rgba(110,217,135,0.42)' : 'rgba(110,217,135,0.16)');
  glow.addColorStop(light ? 0.45 : 0.5, light ? 'rgba(110,217,135,0.16)' : 'rgba(110,217,135,0.06)');
  glow.addColorStop(1, 'rgba(110,217,135,0)');
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, ZONE, 0, 7);
  ctx.fill();

  // body
  edgePath(ctx, sim, cx, cy);
  const body = ctx.createRadialGradient(cx - R * 0.3 + ox, cy - R * 0.35 + oy, R * 0.1, cx, cy, R * 1.15);
  body.addColorStop(0, '#e9fff0');
  body.addColorStop(0.28, '#6ED987');
  body.addColorStop(0.68, '#1BC237');
  body.addColorStop(1, '#0b7a26');
  ctx.save();
  ctx.shadowColor = light ? 'rgba(27,194,55,0.55)' : 'rgba(27,194,55,0.30)';
  ctx.shadowBlur = light ? 34 : 22;
  ctx.fillStyle = body;
  ctx.fill();
  ctx.restore();
  if (light) {
    edgePath(ctx, sim, cx, cy);
    ctx.save();
    ctx.lineWidth = 5;
    ctx.strokeStyle = 'rgba(190,245,205,0.85)';
    ctx.shadowColor = 'rgba(110,217,135,0.9)';
    ctx.shadowBlur = 14;
    ctx.stroke();
    ctx.restore();
  }

  // liquid swirls inside, clipped to the drop
  ctx.save();
  edgePath(ctx, sim, cx, cy);
  ctx.clip();
  TINTS.forEach((tint, k) => {
    const a = t * (0.5 + k * 0.23) + k * 2.1;
    const rr = R * (0.35 + 0.12 * k);
    const sx = cx + Math.cos(a) * rr + ox * 0.5;
    const sy = cy + Math.sin(a * 1.3) * rr * 0.8 + oy * 0.5;
    const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, R * (0.55 + wobble * 0.3));
    g.addColorStop(0, `rgba(${tint.join(',')},${k === 1 ? 0.5 : 0.55})`);
    g.addColorStop(1, `rgba(${tint.join(',')},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, SIZE, SIZE);
  });
  const shade = ctx.createRadialGradient(cx + R * 0.35 - ox * 0.4, cy + R * 0.45 - oy * 0.4, R * 0.2, cx, cy, R * 1.1);
  shade.addColorStop(0, 'rgba(6,90,28,0)');
  shade.addColorStop(1, 'rgba(6,90,28,0.45)');
  ctx.fillStyle = shade;
  ctx.fillRect(0, 0, SIZE, SIZE);
  ctx.restore();

  // specular highlight
  ctx.save();
  ctx.translate(cx - R * 0.32 + ox * 0.9, cy - R * 0.42 + oy * 0.9);
  ctx.rotate(-0.6);
  const sp = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 0.34);
  sp.addColorStop(0, 'rgba(255,255,255,0.95)');
  sp.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.scale(1.5, 0.8);
  ctx.fillStyle = sp;
  ctx.beginPath();
  ctx.arc(0, 0, R * 0.34, 0, 7);
  ctx.fill();
  ctx.restore();
}
