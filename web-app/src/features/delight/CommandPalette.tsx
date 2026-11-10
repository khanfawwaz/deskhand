import { useEffect, useEffectEvent, useId, useState, type KeyboardEvent } from 'react';
import { filterActions, matchesHotkey, type PaletteAction } from './orbit';
import styles from './CommandPalette.module.css';

/** Run `handler` when the shortcut is pressed anywhere on the page ("mod+k" = Ctrl+K, or Cmd+K on a Mac). */
export function useHotkey(combo: string, handler: () => void) {
  const fire = useEffectEvent(handler);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (matchesHotkey(e, combo)) {
        e.preventDefault();
        fire();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [combo]);
}

interface Props {
  open: boolean;
  onClose: () => void;
  actions: PaletteAction[];
}

/** Ctrl+K: jump to anything. Type a few letters, press Enter. Everything here can also be done with the mouse elsewhere. */
export function CommandPalette({ open, onClose, actions }: Props) {
  if (!open) return null;
  return <Panel onClose={onClose} actions={actions} />; // mounted only while open, so it always starts empty
}

function Panel({ onClose, actions }: Omit<Props, 'open'>) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();
  const list = filterActions(query, actions);
  const index = Math.min(active, Math.max(0, list.length - 1));

  const run = (a: PaletteAction | undefined) => {
    if (!a) return;
    onClose();
    a.run();
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(list.length ? (index + 1) % list.length : 0);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(list.length ? (index - 1 + list.length) % list.length : 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      run(list[index]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div className={styles.scrim} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.panel} role="dialog" aria-modal="true" aria-label="Go to or do something">
        <input
          className={styles.input}
          autoFocus
          value={query}
          placeholder="Type what you want to do or find"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={list[index] ? `${listId}-${list[index].id}` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKey}
        />
        <ul className={styles.list} id={listId} role="listbox">
          {list.map((a, i) => (
            <li
              key={a.id}
              id={`${listId}-${a.id}`}
              role="option"
              aria-selected={i === index}
              className={styles.item}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()} /* keep the typing focus */
              onClick={() => run(a)}
            >
              <span className={styles.label}>{a.label}</span>
              {a.hint ? <span className={styles.hint}>{a.hint}</span> : null}
            </li>
          ))}
          {list.length === 0 ? <li className={styles.none}>Nothing matches. Try “theme”, “settings” or the first words of a chat.</li> : null}
        </ul>
        <p className={styles.foot}>↑ ↓ to move, Enter to choose, Esc to close</p>
      </div>
    </div>
  );
}
