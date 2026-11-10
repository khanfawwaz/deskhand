import { useEffect, useEffectEvent, useRef, useState } from 'react';

/** The browser's speech recognition (Chrome, Edge, Safari), if it has one. */
interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}
interface RecognitionEvent {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const VOICE_UNSUPPORTED = 'Voice input needs Chrome, Edge or Safari. Typing works everywhere.';

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone access was blocked. Allow it in the browser to use voice.',
  'service-not-allowed': 'Speech recognition is not allowed here (it needs a secure https page).',
  'no-speech': 'I did not hear anything.',
  'audio-capture': 'No microphone found.',
  network: 'Speech recognition could not reach its service.',
};

interface Options {
  /** a finished sentence */
  onFinal: (text: string) => void;
  /** a line for the status text under the message box ("Listening…", what was heard so far, an error) */
  onStatus: (text: string) => void;
}

/**
 * Voice input: one sentence at a time. Feature-detected: where the browser has no speech recognition, `supported` is
 * false and the mic button is simply not shown.
 */
export function useVoice({ onFinal, onStatus }: Options): {
  supported: boolean;
  listening: boolean;
  toggle: () => void;
} {
  const [listening, setListening] = useState(false);
  const recRef = useRef<Recognition | null>(null);
  const supported = recognitionCtor() !== null;
  const final = useEffectEvent((t: string) => onFinal(t));
  const status = useEffectEvent((t: string) => onStatus(t));

  useEffect(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = navigator.language || 'en-US';
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    let finalText = '';
    rec.onstart = () => {
      finalText = '';
      setListening(true);
      status('Listening…');
    };
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (!r) continue;
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      status('“' + (finalText + interim).trim() + '”');
    };
    rec.onerror = (e) => status(ERRORS[e.error] ?? 'Voice error: ' + e.error);
    rec.onend = () => {
      setListening(false);
      if (finalText.trim()) final(finalText);
    };
    recRef.current = rec;
    return () => {
      rec.onend = null;
      rec.onresult = null;
      try {
        rec.abort();
      } catch {
        /* not started */
      }
      recRef.current = null;
    };
  }, []);

  const toggle = () => {
    const rec = recRef.current;
    if (!rec) return;
    if (listening) {
      rec.stop();
      return;
    }
    try {
      rec.start();
    } catch {
      /* already starting */
    }
  };

  return { supported, listening, toggle };
}
