import { useEffect, useState } from 'react';

/**
 * A placeholder that types out example requests one after another ("Open Notepad and write a shopping list…"), so an
 * empty message box shows what to say instead of sitting still. It stops as soon as `active` is false (the person is
 * typing or a task is running), and people who ask for less motion get the first example, not moving.
 */
export function useTypewriter(lines: string[], active: boolean, opts: { typeMs?: number; holdMs?: number; eraseMs?: number } = {}): string {
  const { typeMs = 42, holdMs = 1900, eraseMs = 16 } = opts;
  const [text, setText] = useState('');
  const key = lines.join('\n');

  useEffect(() => {
    const all = key ? key.split('\n') : [];
    if (!active || all.length === 0) return;
    if (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const t = setTimeout(() => setText(all[0] ?? ''), 0);
      return () => clearTimeout(t);
    }
    let line = 0, shown = 0, erasing = false;
    let timer: ReturnType<typeof setTimeout>;
    const step = () => {
      const full = all[line] ?? '';
      if (!erasing) {
        shown += 1;
        setText(full.slice(0, shown));
        if (shown >= full.length) {
          erasing = true;
          timer = setTimeout(step, holdMs);
          return;
        }
        timer = setTimeout(step, typeMs);
      } else {
        shown -= 2;
        if (shown <= 0) {
          shown = 0;
          erasing = false;
          line = (line + 1) % all.length;
        }
        setText(full.slice(0, shown));
        timer = setTimeout(step, eraseMs);
      }
    };
    timer = setTimeout(step, 600);
    return () => clearTimeout(timer);
  }, [key, active, typeMs, holdMs, eraseMs]);

  return active ? text : '';
}
