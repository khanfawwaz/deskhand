import { useState } from 'react';
import type { TaskView } from '../../api/types';
import { approvalText } from '../../lib/chats';
import styles from './Thread.module.css';

interface Props {
  task: TaskView;
  /** an option was chosen, or text was typed; `label` is what to show in the chat afterwards */
  onAnswer: (taskId: string, body: { choice?: string; text?: string }, label: string) => void;
  onDecide: (approved: boolean) => void;
}

/**
 * A question or a request for permission, answered like in any chat app: option buttons ("Allow this time",
 * "Always allow on this computer", "Not now"), a free-text answer when the question allows it, or Approve / Deny.
 */
export function QuestionCard({ task, onAnswer, onDecide }: Props) {
  const pa = task.pending_approval;
  const [text, setText] = useState('');
  if (!pa) return null;
  const choice = pa.kind === 'access' || pa.kind === 'question';
  const options = pa.options ?? [];
  const title = pa.kind === 'question' ? 'Deskhand has a question' : 'Deskhand needs your OK';
  const hint = choice
    ? pa.kind === 'access'
      ? 'You stay in control: Deskhand only touches what you allow.'
      : ''
    : 'Say "approve" or "deny", or tap a button.';

  return (
    <div className={styles.ask} role="alertdialog" aria-labelledby={`ask-${task.id}`}>
      <strong id={`ask-${task.id}`} className={styles.askTitle}>
        {title}
      </strong>
      <p className={styles.askText}>{approvalText(pa)}</p>
      {choice && options.length > 0 && (
        <div className={styles.choices}>
          {options.map((o, i) => (
            <button
              key={o.id}
              type="button"
              className={o.id === 'no' ? styles.choiceDanger : i === 0 && pa.kind === 'access' ? styles.choicePrimary : undefined}
              onClick={() => onAnswer(task.id, { choice: o.id }, o.label)}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
      {choice && pa.allow_text && (
        <form
          className={styles.answer}
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            const v = text.trim();
            if (!v) return;
            onAnswer(task.id, { text: v }, v);
            setText('');
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={500}
            placeholder="Something else…"
            aria-label="Your own answer"
          />
          <button className={styles.sendMini} type="submit" aria-label="Send answer">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path
                d="M5 12h14M13 6l6 6-6 6"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </form>
      )}
      {!choice && (
        <div className="row">
          <button className="ghost" type="button" onClick={() => onDecide(false)}>
            Deny
          </button>
          <button className="primary" type="button" onClick={() => onDecide(true)}>
            Approve
          </button>
        </div>
      )}
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}
