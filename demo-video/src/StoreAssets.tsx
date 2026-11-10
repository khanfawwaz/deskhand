// Images for the Chrome Web Store listing: five 1280x800 screenshots, a 440x280 small tile and a 1400x560 marquee.
// Rendered as JPEG stills (no transparency), see render-store-assets.mjs.
import React from 'react';
import { AbsoluteFill, Img, staticFile } from 'remotion';

const INK = '#12241d', MUTED = '#4d5f56', GREEN = '#1BC237', LINE = '#DCE1DE';
const FONT = '"Segoe UI", system-ui, -apple-system, sans-serif';
const BG = 'linear-gradient(135deg, #F4F6F5 0%, #E4EFE7 100%)';

const Logo: React.FC<{ size: number; radius?: number }> = ({ size, radius }) => (
  <Img src={staticFile('logo.png')} style={{ width: size, height: size, borderRadius: radius ?? size * 0.23, boxShadow: '0 10px 30px rgba(18,36,29,.22)' }} />
);

// the 1280x800 screenshot layout: logo + words on the left, one picture on the right
const Shot: React.FC<{ title: string; sub: string; children: React.ReactNode; wide?: boolean }> = ({ title, sub, children, wide }) => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT }}>
    <div style={{ position: 'absolute', left: 64, top: 52, display: 'flex', alignItems: 'center', gap: 14 }}>
      <Logo size={52} />
      <span style={{ fontSize: 30, fontWeight: 750, color: INK, letterSpacing: -0.5 }}>Deskhand</span>
    </div>
    <div style={{ position: 'absolute', left: 64, top: wide ? 150 : 250, width: wide ? 1150 : 480 }}>
      <div style={{ fontSize: wide ? 60 : 56, fontWeight: 760, color: INK, lineHeight: 1.08, letterSpacing: -1.5 }}>{title}</div>
      <div style={{ fontSize: 28, color: MUTED, lineHeight: 1.4, marginTop: 22 }}>{sub}</div>
    </div>
    {children}
  </AbsoluteFill>
);

const Card: React.FC<{ style?: React.CSSProperties; children: React.ReactNode }> = ({ style, children }) => (
  <div style={{ position: 'absolute', background: '#fff', border: `1px solid ${LINE}`, borderRadius: 24, boxShadow: '0 28px 70px rgba(18,36,29,.2)', overflow: 'hidden', ...style }}>{children}</div>
);

const Popup: React.FC<{ src: string; style?: React.CSSProperties }> = ({ src, style }) => (
  <div style={{ position: 'absolute', width: 600, height: 585, borderRadius: 22, overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,.35)', border: '1px solid #2b2a46', ...style }}>
    <Img src={staticFile('shots/' + src)} style={{ width: '100%', height: '100%', display: 'block' }} />
  </div>
);

export const Shot1: React.FC = () => (
  <Shot title="Connect once, from the popup" sub="Press Connect to Deskhand. Nothing happens until you approve it on your computer.">
    <Popup src="popup-1-crop.png" style={{ right: 70, top: 60, height: 668 }} />
  </Shot>
);

export const Shot2: React.FC = () => (
  <Shot wide title="You approve it, on your computer" sub="The Deskhand app asks Yes or No. No is selected by default.">
    <Card style={{ left: 150, top: 400, width: 980, borderRadius: 14, border: '1px solid #cfcfcf', background: '#f3f3f3' }}>
      <div style={{ background: '#fff', padding: '14px 22px', fontSize: 24, color: '#222', borderBottom: '1px solid #e2e2e2' }}>Deskhand: connect Chrome?</div>
      <div style={{ display: 'flex', gap: 26, padding: '28px 32px', background: '#fff' }}>
        <div style={{ width: 58, height: 58, borderRadius: 29, background: '#2b79d7', color: '#fff', fontSize: 40, fontWeight: 700, display: 'grid', placeItems: 'center', flex: 'none' }}>?</div>
        <div style={{ fontSize: 25, lineHeight: 1.45, color: '#1b1b1b' }}>
          A Chrome extension is asking to connect to Deskhand so it can carry out your web tasks in your browser.
          <div style={{ marginTop: 12 }}>Only choose Yes if you just pressed 'Connect to Deskhand' in Chrome.</div>
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 14, padding: '18px 26px', background: '#f3f3f3' }}>
        <div style={{ width: 130, textAlign: 'center', padding: '10px 0', fontSize: 24, border: '2px solid #8a8a8a', background: '#e9e9e9' }}>Yes</div>
        <div style={{ width: 130, textAlign: 'center', padding: '10px 0', fontSize: 24, border: '3px solid #0067c0', background: '#e9e9e9' }}>No</div>
      </div>
    </Card>
  </Shot>
);

export const Shot3: React.FC = () => (
  <Shot title="Connected, and you stay in control" sub="Disconnect this browser from the popup at any time.">
    <Popup src="popup-connected-crop.png" style={{ right: 70, top: 100 }} />
  </Shot>
);

const Win: React.FC<{ x: number; y: number; w: number; h: number; z: number; tabs: string[]; bar?: boolean; children: React.ReactNode }> = ({ x, y, w, h, z, tabs, bar, children }) => (
  <div style={{ position: 'absolute', left: x, top: y, width: w, height: h, zIndex: z, background: '#fff', borderRadius: 16, overflow: 'hidden', boxShadow: '0 26px 70px rgba(18,36,29,.24)', border: `1px solid ${LINE}` }}>
    <div style={{ height: 46, background: '#DEE1E6', display: 'flex', alignItems: 'flex-end', gap: 6, padding: '0 14px' }}>
      {tabs.map((t, i) => <div key={t} style={{ padding: '8px 18px', borderRadius: '10px 10px 0 0', background: i === 0 ? '#fff' : 'transparent', fontSize: 18, color: '#3c4043' }}>{t}</div>)}
    </div>
    {bar ? <div style={{ background: '#fff8e1', borderBottom: '1px solid #f0e0a0', padding: '7px 16px', fontSize: 17, color: '#5f4b00' }}>“Deskhand” started debugging this browser</div> : null}
    <div style={{ padding: 20 }}>{children}</div>
  </div>
);

export const Shot4: React.FC = () => (
  <Shot wide title="It works in its own window" sub="Your other tabs are never touched. Chrome shows its standard debugging bar while a task runs.">
    <Win x={640} y={380} w={560} h={340} z={1} tabs={['youtube.com']} bar>
      {['Opened a web page', 'Searched the site', 'Looked at the links', 'Clicked the video'].map((t) => (
        <div key={t} style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 24, color: INK, padding: '6px 0' }}>
          <span style={{ width: 26, height: 26, borderRadius: 13, background: GREEN, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 16, fontWeight: 800 }}>✓</span>{t}
        </div>
      ))}
    </Win>
    <Win x={110} y={470} w={540} h={290} z={2} tabs={['Inbox', 'Docs: report', 'Calendar']}>
      <div style={{ fontSize: 24, color: MUTED, lineHeight: 1.45 }}>Your own tabs stay exactly as they are.</div>
      <div style={{ marginTop: 18, height: 12, width: '70%', background: LINE, borderRadius: 6 }} />
      <div style={{ marginTop: 10, height: 12, width: '52%', background: LINE, borderRadius: 6 }} />
    </Win>
  </Shot>
);

export const Shot5: React.FC = () => (
  <Shot wide title="See every step, in plain words" sub="Ask in the Deskhand app. The extension carries out the web steps in your signed-in Chrome.">
    <Card style={{ left: 140, top: 330, width: 1000, height: 440 }}>
      <Img src={staticFile('shots/07-done.png')} style={{ width: 1000, height: 625, display: 'block', objectFit: 'cover', objectPosition: 'top' }} />
    </Card>
  </Shot>
);

// 440x280 small promo tile
export const Tile: React.FC = () => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT, alignItems: 'center', justifyContent: 'center' }}>
    <Logo size={104} />
    <div style={{ fontSize: 40, fontWeight: 780, color: INK, letterSpacing: -1, marginTop: 14 }}>Deskhand for Chrome</div>
    <div style={{ fontSize: 20, color: MUTED, marginTop: 4, textAlign: 'center', padding: '0 24px' }}>Web tasks in your own signed-in browser</div>
  </AbsoluteFill>
);

// 1400x560 marquee promo tile
export const Marquee: React.FC = () => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT }}>
    <div style={{ position: 'absolute', left: 80, top: 100 }}>
      <Logo size={110} />
      <div style={{ fontSize: 60, fontWeight: 780, color: INK, letterSpacing: -1.5, marginTop: 24, lineHeight: 1.04, whiteSpace: 'nowrap' }}>Deskhand for Chrome</div>
      <div style={{ fontSize: 34, color: MUTED, marginTop: 14, width: 640, lineHeight: 1.3 }}>Your browser. Your accounts. Your approval.</div>
    </div>
    <Popup src="popup-connected-crop.png" style={{ right: 70, top: 30, width: 500, height: 488 }} />
  </AbsoluteFill>
);

// 1280x720 YouTube thumbnail for the Chrome review video
export const Thumb: React.FC = () => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT }}>
    <div style={{ position: 'absolute', left: 70, top: 70, display: 'flex', alignItems: 'center', gap: 18 }}>
      <Logo size={84} />
      <span style={{ fontSize: 46, fontWeight: 780, color: INK, letterSpacing: -1 }}>Deskhand</span>
    </div>
    <div style={{ position: 'absolute', left: 70, top: 220, width: 640 }}>
      <div style={{ fontSize: 104, fontWeight: 800, color: INK, lineHeight: 1.0, letterSpacing: -3 }}>Chrome extension</div>
      <div style={{ fontSize: 52, fontWeight: 600, color: MUTED, marginTop: 26, lineHeight: 1.15 }}>How it works, in 80 seconds</div>
      <div style={{ display: 'inline-block', marginTop: 34, background: GREEN, color: '#fff', fontSize: 36, fontWeight: 700, padding: '12px 32px', borderRadius: 999 }}>You approve everything</div>
    </div>
    <Popup src="popup-connected-crop.png" style={{ right: 60, top: 70, width: 520, height: 507 }} />
  </AbsoluteFill>
);
