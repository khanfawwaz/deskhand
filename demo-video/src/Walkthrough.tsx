import React from 'react';
import { AbsoluteFill, Audio, Easing, Freeze, Img, OffthreadVideo, Series, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
type Scene = {
  nodes?: { title: string; sub: string }[]; link?: string; perms?: { name: string; why: string }[]; chips?: string[];
  popup?: string;
  id: string; kind: string; label: string; text: string; audio: string; frames: number; narrationSeconds: number;
  image?: string; focus?: { x: number; y: number; scale: number };
  clip?: string; speed?: number; clipFrames?: number;
  headline?: string; sub?: string; typed?: string; points?: string[];
};

const INK = '#12241d', MUTED = '#5d6d65', GREEN = '#1BC237', MINT = '#6ED987', BG = '#F4F6F5', LINE = '#DCE1DE';
const FONT = '"Segoe UI", system-ui, -apple-system, sans-serif';

// ------------------------------------------------------------------ shared pieces
const Label: React.FC<{ text: string }> = ({ text }) => {
  const f = useCurrentFrame();
  if (!text) return null;
  const o = interpolate(f, [0, 14], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <div style={{ position: 'absolute', top: 34, left: 0, right: 0, display: 'flex', justifyContent: 'center', opacity: o }}>
      <div style={{ fontFamily: FONT, fontSize: 30, fontWeight: 650, color: INK, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 999, padding: '10px 28px', boxShadow: '0 6px 24px rgba(18,36,29,.08)' }}>
        <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 6, background: GREEN, marginRight: 14 }} />
        {text}
      </div>
    </div>
  );
};

// the narration as subtitles: each sentence is on screen while it is being spoken
const Captions: React.FC<{ text: string; seconds: number }> = ({ text, seconds }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const parts = text.match(/[^.!?]+[.!?]+/g) || [text];
  const total = parts.reduce((a, p) => a + p.length, 0);
  const t = f / fps;
  let acc = 0, current = parts[0].trim();
  for (const p of parts) {
    const start = (acc / total) * seconds;
    if (t >= start - 0.05) current = p.trim();
    acc += p.length;
  }
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, bottom: 34, display: 'flex', justifyContent: 'center' }}>
      <div style={{ maxWidth: 1560, fontFamily: FONT, fontSize: 33, lineHeight: 1.28, textAlign: 'center', color: '#fff', background: 'rgba(18,36,29,.88)', borderRadius: 18, padding: '14px 30px' }}>{current}</div>
    </div>
  );
};

// the logo, small, in the corner of every scene
const Brand: React.FC = () => (
  <div style={{ position: 'absolute', top: 30, left: 40, display: 'flex', alignItems: 'center', gap: 14 }}>
    <Img src={staticFile('logo.png')} style={{ width: 56, height: 56, borderRadius: 14, boxShadow: '0 6px 18px rgba(18,36,29,.18)' }} />
    <span style={{ fontFamily: FONT, fontSize: 30, fontWeight: 700, color: INK, letterSpacing: -0.5 }}>Deskhand</span>
  </div>
);

const Frame: React.FC<{ s: Scene; children: React.ReactNode }> = ({ s, children }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const o = interpolate(f, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill style={{ background: BG, opacity: o, fontFamily: FONT }}>
      <Audio src={staticFile(s.audio)} />
      <Label text={s.label} />
      <Brand />
      {children}
      <Captions text={s.text} seconds={s.narrationSeconds} />
    </AbsoluteFill>
  );
};

const Orb: React.FC<{ size: number }> = ({ size }) => {
  const f = useCurrentFrame();
  const wob = 1 + Math.sin(f / 18) * 0.025;
  return (
    <div style={{ width: size * 1.9, height: size * 1.9, display: 'grid', placeItems: 'center', background: 'radial-gradient(circle, rgba(110,217,135,.45) 0%, rgba(110,217,135,.12) 45%, rgba(110,217,135,0) 70%)' }}>
      <div style={{ width: size, height: size, borderRadius: '50%', transform: `scale(${wob}, ${2 - wob})`, boxShadow: `0 0 ${size * 0.35}px rgba(27,194,55,.55), inset 0 0 0 4px rgba(190,245,205,.85)`,
        background: `radial-gradient(circle at 34% 30%, #f1fff5 0%, ${MINT} 26%, ${GREEN} 66%, #0b7a26 100%)` }} />
    </div>
  );
};

// ------------------------------------------------------------------ scene kinds
const Title: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const up = interpolate(f, [0, 30], [30, 0], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
  return (
    <Frame s={{ ...s, label: '' }}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', paddingBottom: 90 }}>
        <div style={{ position: 'relative', width: 420, height: 420, display: 'grid', placeItems: 'center' }}>
          <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(circle, rgba(110,217,135,.45) 0%, rgba(110,217,135,.12) 45%, rgba(110,217,135,0) 70%)' }} />
          <Img src={staticFile('logo.png')} style={{ width: 230, height: 230, borderRadius: 52, transform: `scale(${spring({ frame: f, fps: 30, config: { damping: 12 } })})`, boxShadow: '0 24px 60px rgba(18,36,29,.28)' }} />
        </div>
        <div style={{ transform: `translateY(${up}px)`, textAlign: 'center', marginTop: -10 }}>
          <div style={{ fontSize: 110, fontWeight: 750, color: INK, letterSpacing: -2 }}>{s.label}</div>
          <div style={{ fontSize: 44, color: MUTED, marginTop: 10, maxWidth: 1400 }}>{s.headline}</div>
          {s.link ? <div style={{ fontSize: 38, color: INK, marginTop: 30, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 999, padding: '12px 34px', display: 'inline-block', boxShadow: '0 8px 30px rgba(18,36,29,.1)' }}>{s.link}</div> : null}
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

const Shot: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const fx = s.focus || { x: 50, y: 50, scale: 1.2 };
  const k = interpolate(f, [12, Math.min(durationInFrames * 0.4, 120)], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic) });
  const scale = 1 + (fx.scale - 1) * k;
  const W = 1280, H = 800;                                           // the screenshots are 16:10; sits between the label and the captions
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-start', paddingTop: 112 }}>
        <div style={{ width: W, height: H, borderRadius: 22, overflow: 'hidden', background: '#fff', border: `1px solid ${LINE}`, boxShadow: '0 30px 80px rgba(18,36,29,.18)', position: 'relative' }}>
          <Img src={staticFile('shots/' + s.image)} style={{ width: W, height: H, transformOrigin: `${fx.x}% ${fx.y}%`, transform: `scale(${scale})`, display: 'block' }} />
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

// a screen recording of the real app (cursor, typing, clicks are all in the clip), sped up to keep it quick
const Clip: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const rate = s.speed || 1;
  const clipFrames = s.clipFrames || s.frames;
  const W = 1280, H = 800;
  const drift = interpolate(f, [0, s.frames], [1, 1.035]);           // a slow push-in so it never feels like a still
  const vid = <OffthreadVideo src={staticFile('clips/' + s.clip + '.mp4')} playbackRate={rate} muted style={{ width: W, height: H, display: 'block', transform: `scale(${drift})`, transformOrigin: '50% 40%' }} />;
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-start', paddingTop: 112 }}>
        <div style={{ width: W, height: H, borderRadius: 22, overflow: 'hidden', background: '#fff', border: `1px solid ${LINE}`, boxShadow: '0 30px 80px rgba(18,36,29,.18)' }}>
          {f >= clipFrames ? <Freeze frame={Math.max(0, clipFrames - 1)}>{vid}</Freeze> : vid}
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

const Key: React.FC<{ children: React.ReactNode; down: number }> = ({ children, down }) => (
  <div style={{ minWidth: 150, height: 150, borderRadius: 28, background: '#fff', border: `2px solid ${LINE}`, display: 'grid', placeItems: 'center', fontSize: 64, fontWeight: 700, color: INK,
    boxShadow: down ? '0 2px 0 #cfd6d2' : '0 12px 0 #cfd6d2, 0 20px 40px rgba(18,36,29,.12)', transform: `translateY(${down ? 10 : 0}px)`, padding: '0 28px' }}>{children}</div>
);

const Voice: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pressed = f > fps * 0.4 ? 1 : 0;
  const typedAt = fps * 1.0;
  const n = Math.max(0, Math.floor((f - typedAt) / 1.6));
  const typed = (s.typed || '').slice(0, n);
  const sent = f > typedAt + (s.typed || '').length * 1.6 + fps * 0.5;
  const bars = Array.from({ length: 28 }, (_, i) => 14 + Math.abs(Math.sin(f / 5 + i * 0.7)) * (f > typedAt - 20 && !sent ? 70 : 8));
  const pop = spring({ frame: f - fps * 0.5, fps, config: { damping: 14 } });
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 120, paddingBottom: 90, paddingTop: 40 }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ display: 'flex', gap: 28, alignItems: 'center', justifyContent: 'center' }}>
            <Key down={pressed}>Alt</Key><span style={{ fontSize: 56, color: MUTED }}>+</span><Key down={pressed}>F8</Key>
          </div>
          <div style={{ fontSize: 42, color: MUTED, marginTop: 56 }}>{s.sub}</div>
        </div>
        <div style={{ width: 760, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 28, boxShadow: '0 30px 80px rgba(18,36,29,.16)', padding: 40, opacity: Math.min(1, pop), transform: `scale(${0.92 + 0.08 * Math.min(1, pop)})` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 18, color: MUTED, fontSize: 28, fontWeight: 600 }}>
            <span style={{ width: 18, height: 18, borderRadius: 9, background: sent ? GREEN : '#e5484d', display: 'inline-block' }} />{sent ? 'Sent to your computer' : 'Listening…'}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, height: 100, marginTop: 20 }}>
            {bars.map((h, i) => <div key={i} style={{ flex: 1, height: h, borderRadius: 6, background: sent ? LINE : MINT }} />)}
          </div>
          <div style={{ minHeight: 80, fontSize: 40, color: INK, marginTop: 18 }}>“{typed}<span style={{ opacity: sent ? 0 : (f % 20 < 10 ? 1 : 0) }}>|</span>”</div>
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

const Together: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const steps = ['Opened a web page', 'Searched the site', 'Looked at the links', 'Clicked the video', 'Checked the address'];
  const shown = Math.floor(Math.max(0, f - fps * 0.5) / (fps * 0.65));
  const typing = 'Meeting notes: send the report by Friday'.slice(0, Math.floor(f / 1.4));
  const Win: React.FC<{ title: string; tone: string; children: React.ReactNode }> = ({ title, tone, children }) => (
    <div style={{ width: 760, height: 520, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 22, boxShadow: '0 30px 80px rgba(18,36,29,.16)', overflow: 'hidden' }}>
      <div style={{ height: 64, background: tone, display: 'flex', alignItems: 'center', padding: '0 26px', fontSize: 28, fontWeight: 650, color: INK, borderBottom: `1px solid ${LINE}` }}>{title}</div>
      <div style={{ padding: 30 }}>{children}</div>
    </div>
  );
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 70, paddingBottom: 100, paddingTop: 30 }}>
        <Win title="Your window: you keep working" tone="#EEF1EF">
          <div style={{ fontSize: 34, color: INK, lineHeight: 1.5 }}>{typing}<span style={{ opacity: f % 20 < 10 ? 1 : 0 }}>|</span></div>
        </Win>
        <Win title="Deskhand's own window" tone="#DBE5DD">
          {steps.map((t, i) => (
            <div key={t} style={{ display: 'flex', gap: 16, alignItems: 'center', fontSize: 32, color: i < shown ? INK : '#b5c0ba', padding: '9px 0', opacity: i <= shown ? 1 : 0.35 }}>
              <span style={{ width: 30, height: 30, borderRadius: 15, background: i < shown ? GREEN : LINE, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 20, fontWeight: 800 }}>{i < shown ? '✓' : ''}</span>{t}
            </div>
          ))}
          <div style={{ marginTop: 22, fontSize: 26, color: MUTED }}>Waits politely when you type or click.</div>
        </Win>
      </AbsoluteFill>
    </Frame>
  );
};

const Control: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', paddingBottom: 90, paddingTop: 30 }}>
        <div style={{ fontSize: 66, fontWeight: 750, color: INK, textAlign: 'center', maxWidth: 1600, lineHeight: 1.12, letterSpacing: -1, whiteSpace: 'pre-line' }}>{s.headline}</div>
        <div style={{ marginTop: 60, display: 'grid', gap: 26 }}>
          {(s.points || []).map((p, i) => {
            const a = spring({ frame: f - fps * (0.7 + i * 1.3), fps, config: { damping: 16 } });
            return (
              <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 26, fontSize: 46, color: INK, opacity: Math.min(1, a), transform: `translateX(${(1 - Math.min(1, a)) * 60}px)`, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 22, padding: '20px 36px', boxShadow: '0 12px 40px rgba(18,36,29,.08)' }}>
                <span style={{ width: 54, height: 54, borderRadius: 27, background: GREEN, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 34, fontWeight: 800 }}>✓</span>{p}
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

// ------------------------------------------------------------------ the Chrome extension
const Arrow: React.FC<{ on: number }> = ({ on }) => (
  <div style={{ fontSize: 64, color: GREEN, opacity: on, transform: `translateX(${(1 - on) * -14}px)`, fontWeight: 800 }}>›</div>
);

// four boxes joined by arrows, lighting up one after another
const Flow: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const nodes = s.nodes || [];
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', paddingBottom: 90, paddingTop: 60 }}>
        <div style={{ fontSize: 62, fontWeight: 750, color: INK, marginBottom: 56, letterSpacing: -1 }}>{s.headline}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          {nodes.map((n, i) => {
            const a = spring({ frame: f - fps * (0.4 + i * 0.7), fps, config: { damping: 15 } });
            return (
              <React.Fragment key={n.title}>
                <div style={{ width: 380, minHeight: 250, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 26, padding: '34px 30px', boxShadow: '0 20px 60px rgba(18,36,29,.12)', opacity: Math.min(1, a), transform: `translateY(${(1 - Math.min(1, a)) * 40}px)` }}>
                  <div style={{ width: 54, height: 54, borderRadius: 27, background: i === nodes.length - 1 ? GREEN : INK, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 28, fontWeight: 800 }}>{i + 1}</div>
                  <div style={{ fontSize: 38, fontWeight: 700, color: INK, marginTop: 18 }}>{n.title}</div>
                  <div style={{ fontSize: 28, color: MUTED, marginTop: 8, lineHeight: 1.3 }}>{n.sub}</div>
                </div>
                {i < nodes.length - 1 ? <Arrow on={Math.min(1, a)} /> : null}
              </React.Fragment>
            );
          })}
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

// the extension's popup (the real one, recorded) under the Deskhand icon in a Chrome toolbar
const PopupClip: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const rate = s.speed || 1;
  const clipFrames = s.clipFrames || s.frames;
  const vid = <OffthreadVideo src={staticFile('clips/' + s.clip + '.mp4')} playbackRate={rate} muted style={{ width: 600, height: 550, display: 'block' }} />;
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-start', paddingTop: 112 }}>
        <div style={{ width: 1280, height: 800, borderRadius: 22, overflow: 'hidden', background: '#fff', border: `1px solid ${LINE}`, boxShadow: '0 30px 80px rgba(18,36,29,.18)', position: 'relative' }}>
          <div style={{ height: 78, background: '#DEE1E6', display: 'flex', alignItems: 'center', gap: 16, padding: '0 22px' }}>
            <div style={{ display: 'flex', gap: 9 }}>{['#ff5f57', '#febc2e', '#28c840'].map((c) => <i key={c} style={{ width: 16, height: 16, borderRadius: 8, background: c, display: 'block' }} />)}</div>
            <div style={{ flex: 1, height: 44, borderRadius: 22, background: '#fff', display: 'flex', alignItems: 'center', padding: '0 22px', fontSize: 24, color: '#5f6368' }}>deskhand.fawwazkhan.dev</div>
            <Img src={staticFile('logo.png')} style={{ width: 40, height: 40, borderRadius: 10, outline: `3px solid ${GREEN}`, outlineOffset: 3 }} />
          </div>
          <div style={{ position: 'absolute', top: 92, right: 40, width: 600, height: 550, borderRadius: 18, overflow: 'hidden', boxShadow: '0 24px 70px rgba(0,0,0,.35)', border: '1px solid #2b2a46' }}>
            {f >= clipFrames ? <Freeze frame={Math.max(0, clipFrames - 1)}>{vid}</Freeze> : vid}
          </div>
          <div style={{ position: 'absolute', left: 60, top: 190, width: 560, fontSize: 40, lineHeight: 1.3, color: INK, fontWeight: 600 }}>{s.headline}</div>
          <div style={{ position: 'absolute', left: 60, top: 330, width: 560, fontSize: 28, lineHeight: 1.4, color: MUTED }}>{s.sub}</div>
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

// the Yes / No box the Deskhand app shows on the computer (its real wording)
const Approval: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame: f - fps * 0.3, fps, config: { damping: 14 } });
  const move = interpolate(f, [fps * 2.0, fps * 3.0], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic) });
  const clicked = f > fps * 3.1;
  const cx = 1560 + (1212 - 1560) * move, cy = 860 + (642 - 860) * move;   // cursor path from the corner to the middle of "Yes"
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', paddingBottom: 80, paddingTop: 50 }}>
        <div style={{ width: 1080, background: '#f3f3f3', borderRadius: 14, boxShadow: '0 40px 100px rgba(0,0,0,.28)', border: '1px solid #cfcfcf', overflow: 'hidden', opacity: Math.min(1, pop), transform: `scale(${0.94 + 0.06 * Math.min(1, pop)})` }}>
          <div style={{ background: '#fff', padding: '16px 22px', fontSize: 26, color: '#222', borderBottom: '1px solid #e2e2e2', fontFamily: '"Segoe UI", sans-serif' }}>Deskhand: connect Chrome?</div>
          <div style={{ display: 'flex', gap: 28, padding: '34px 36px', fontFamily: '"Segoe UI", sans-serif', background: '#fff' }}>
            <div style={{ width: 64, height: 64, borderRadius: 32, background: '#2b79d7', color: '#fff', fontSize: 44, fontWeight: 700, display: 'grid', placeItems: 'center', flex: 'none' }}>?</div>
            <div style={{ fontSize: 27, lineHeight: 1.45, color: '#1b1b1b' }}>
              A Chrome extension is asking to connect to Deskhand so it can carry out your web tasks in your browser.
              <div style={{ marginTop: 16 }}>Only choose Yes if you just pressed 'Connect to Deskhand' in Chrome.</div>
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 16, padding: '22px 30px', background: '#f3f3f3' }}>
            <div style={{ width: 150, textAlign: 'center', padding: '12px 0', fontSize: 26, border: `2px solid ${clicked ? '#0a7a2f' : '#8a8a8a'}`, background: clicked ? '#d6f3df' : '#e9e9e9', color: '#111', fontFamily: '"Segoe UI", sans-serif' }}>Yes</div>
            <div style={{ width: 150, textAlign: 'center', padding: '12px 0', fontSize: 26, border: `3px solid ${clicked ? '#8a8a8a' : '#0067c0'}`, background: '#e9e9e9', color: '#111', fontFamily: '"Segoe UI", sans-serif' }}>No</div>
          </div>
        </div>
        <svg width="40" height="40" viewBox="0 0 24 24" style={{ position: 'absolute', left: cx, top: cy, filter: 'drop-shadow(0 3px 4px rgba(0,0,0,.45))' }}><path d="M4 2l15 9-6.5 1.6L9 19z" fill="#fff" stroke="#12241d" strokeWidth="1.6" strokeLinejoin="round" /></svg>
        {s.sub ? <div style={{ position: 'absolute', bottom: 150, fontSize: 34, color: MUTED }}>{clicked ? 'Approved by you, on your computer.' : s.sub}</div> : null}
      </AbsoluteFill>
    </Frame>
  );
};

// the person's own Chrome window stays in front; Deskhand works in a separate one behind it
const AgentWindow: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const steps = ['Opened a web page', 'Searched the site', 'Looked at the links', 'Clicked the video'];
  const shown = Math.floor(Math.max(0, f - fps * 1.2) / (fps * 0.8));
  const Win: React.FC<{ x: number; y: number; w: number; h: number; z: number; title: string; tabs: string[]; children: React.ReactNode; bar?: boolean }> = ({ x, y, w, h, z, title, tabs, children, bar }) => (
    <div style={{ position: 'absolute', left: x, top: y, width: w, height: h, zIndex: z, background: '#fff', borderRadius: 18, overflow: 'hidden', boxShadow: '0 30px 80px rgba(18,36,29,.22)', border: `1px solid ${LINE}` }}>
      <div style={{ height: 54, background: '#DEE1E6', display: 'flex', alignItems: 'flex-end', gap: 8, padding: '0 18px' }}>
        {tabs.map((t, i) => <div key={t} style={{ padding: '9px 22px', borderRadius: '12px 12px 0 0', background: i === 0 ? '#fff' : 'transparent', fontSize: 20, color: '#3c4043' }}>{t}</div>)}
      </div>
      {bar ? <div style={{ background: '#fff8e1', borderBottom: '1px solid #f0e0a0', padding: '8px 20px', fontSize: 20, color: '#5f4b00' }}>“Deskhand” started debugging this browser</div> : null}
      <div style={{ padding: 24 }}><div style={{ fontSize: 24, fontWeight: 650, color: INK, marginBottom: 12 }}>{title}</div>{children}</div>
    </div>
  );
  return (
    <Frame s={s}>
      <AbsoluteFill>
        <Win x={940} y={170} w={900} h={560} z={1} title="Deskhand's own window" tabs={['youtube.com']} bar>
          {steps.map((t, i) => (
            <div key={t} style={{ display: 'flex', gap: 14, alignItems: 'center', fontSize: 28, color: i < shown ? INK : '#b5c0ba', padding: '8px 0' }}>
              <span style={{ width: 28, height: 28, borderRadius: 14, background: i < shown ? GREEN : LINE, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 18, fontWeight: 800 }}>{i < shown ? '✓' : ''}</span>{t}
            </div>
          ))}
        </Win>
        <Win x={90} y={290} w={800} h={540} z={2} title="Your Chrome window" tabs={['Inbox', 'Docs: report', 'Calendar']}>
          <div style={{ fontSize: 26, color: MUTED, lineHeight: 1.5 }}>Your tabs stay exactly as they are.<br />Deskhand never reads or changes them.</div>
          <div style={{ marginTop: 22, height: 14, width: '70%', background: LINE, borderRadius: 7 }} />
          <div style={{ marginTop: 12, height: 14, width: '55%', background: LINE, borderRadius: 7 }} />
          <div style={{ marginTop: 12, height: 14, width: '62%', background: LINE, borderRadius: 7 }} />
        </Win>
        <div style={{ position: 'absolute', left: 940, top: 770, width: 880, fontSize: 30, color: INK, lineHeight: 1.35, fontWeight: 600 }}>Chrome shows its standard debugging bar the whole time a task runs.</div>
      </AbsoluteFill>
    </Frame>
  );
};

const Permissions: React.FC<{ s: Scene }> = ({ s }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <Frame s={s}>
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', paddingBottom: 90, paddingTop: 70 }}>
        <div style={{ fontSize: 58, fontWeight: 750, color: INK, marginBottom: 36, letterSpacing: -1 }}>{s.headline}</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22, width: 1500 }}>
          {(s.perms || []).map((p, i) => {
            const a = spring({ frame: f - fps * (0.5 + i * 0.9), fps, config: { damping: 15 } });
            return (
              <div key={p.name} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 22, padding: '24px 30px', boxShadow: '0 14px 44px rgba(18,36,29,.09)', opacity: Math.min(1, a), transform: `translateY(${(1 - Math.min(1, a)) * 30}px)` }}>
                <code style={{ fontSize: 34, fontWeight: 700, color: INK, background: '#E8F5EC', padding: '4px 14px', borderRadius: 10 }}>{p.name}</code>
                <div style={{ fontSize: 28, color: MUTED, marginTop: 12, lineHeight: 1.3 }}>{p.why}</div>
              </div>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 18, marginTop: 34 }}>
          {(s.chips || []).map((c, i) => {
            const a = interpolate(f, [fps * (4.4 + i * 0.4), fps * (4.8 + i * 0.4)], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
            return <div key={c} style={{ opacity: a, fontSize: 30, color: '#fff', background: INK, borderRadius: 999, padding: '10px 28px' }}>✓ {c}</div>;
          })}
        </div>
      </AbsoluteFill>
    </Frame>
  );
};

const pick: Record<string, React.FC<{ s: Scene }>> = { title: Title, shot: Shot, clip: Clip, voice: Voice, together: Together, control: Control,
  flow: Flow, popupclip: PopupClip, approval: Approval, agentwindow: AgentWindow, permissions: Permissions };

export const Video: React.FC<{ scenes: Scene[] }> = ({ scenes }) => (
  <Series>
    {scenes.map((s) => {
      const C = pick[s.kind] || Shot;
      return (
        <Series.Sequence key={s.id} durationInFrames={s.frames}>
          <C s={s} />
        </Series.Sequence>
      );
    })}
  </Series>
);
