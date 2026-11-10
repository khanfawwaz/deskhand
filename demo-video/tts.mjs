// Makes the narration with Kokoro (a local neural voice: runs on this computer, nothing is sent anywhere) for every scene in
// scripts/<name>.json and writes src/scenes-<name>.json with each scene's length: the longer of the screen clip (sped up) and
// the narration, never shorter than "min". A scene may carry "say" (what is spoken) when it differs from "text" (the caption).
//   node capture-video.mjs   first (it writes clips.json), then   node tts.mjs github   and   node tts.mjs store
// One-time setup: see synth_kokoro.py (venv in .tts-venv, model files in models/).
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const name = process.argv[2];
if (!name) throw new Error('usage: node tts.mjs github|store');
const script = JSON.parse(fs.readFileSync(path.join(here, 'scripts', name + '.json'), 'utf8'));
const clips = fs.existsSync(path.join(here, 'clips.json')) ? JSON.parse(fs.readFileSync(path.join(here, 'clips.json'), 'utf8')) : {};
const audioDir = path.join(here, 'public', 'audio', name);
fs.mkdirSync(audioDir, { recursive: true });
const FPS = 30, PAD = 0.45;
const VOICE = process.env.DESKHAND_VOICE || 'af_heart', SPEED = process.env.DESKHAND_VOICE_SPEED || '1.08';
const py = path.join(here, '.tts-venv', 'Scripts', 'python.exe');
if (!fs.existsSync(py)) throw new Error('the voice is not set up: create .tts-venv and install kokoro-onnx (see synth_kokoro.py)');

const wavSeconds = (file) => {                               // PCM wav: duration = data bytes / byte rate
  const b = fs.readFileSync(file);
  const byteRate = b.readUInt32LE(28);
  let o = 12;
  while (o < b.length - 8) {
    const id = b.toString('ascii', o, o + 4), size = b.readUInt32LE(o + 4);
    if (id === 'data') return Math.min(size, b.length - o - 8) / byteRate;
    o += 8 + size + (size % 2);
  }
  throw new Error('no data chunk in ' + file);
};

// one run of the voice for the whole video (loading the model once)
const jobs = script.map((s) => ({ text: s.say || s.text, out: path.join(audioDir, s.id + '.wav') }));
const jobsFile = path.join(audioDir, '_jobs.json');
fs.writeFileSync(jobsFile, JSON.stringify(jobs), 'utf8');
execFileSync(py, [path.join(here, 'synth_kokoro.py'), '--jobs', jobsFile, '--voice', VOICE, '--speed', SPEED], { stdio: ['ignore', 'ignore', 'pipe'] });
fs.rmSync(jobsFile, { force: true });

const scenes = [];
for (const s of script) {
  const dur = wavSeconds(path.join(audioDir, s.id + '.wav'));
  const clipSeconds = s.clip ? clips[s.clip] : 0;
  if (s.clip && !clipSeconds) throw new Error('no clip recorded for ' + s.clip + ' (run node capture-video.mjs)');
  const clipLen = s.clip ? clipSeconds / (s.speed || 1) : 0;
  const seconds = Math.max(clipLen, dur + PAD, s.min || 0);
  scenes.push({ ...s, audio: 'audio/' + name + '/' + s.id + '.wav', clipSeconds, clipFrames: Math.floor(clipLen * FPS), frames: Math.ceil(seconds * FPS), narrationSeconds: Number(dur.toFixed(2)) });
  console.log(s.id.padEnd(14), 'narration', dur.toFixed(1) + 's', ' scene', seconds.toFixed(1) + 's');
}
fs.mkdirSync(path.join(here, 'src'), { recursive: true });
fs.writeFileSync(path.join(here, 'src', 'scenes-' + name + '.json'), JSON.stringify({ fps: FPS, scenes }, null, 2));
console.log('total', (scenes.reduce((a, s) => a + s.frames, 0) / FPS).toFixed(0) + 's');
