import { useEffect, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import { VOICE_UNSUPPORTED } from '../../hooks/useVoice';
import { moodFor, runningTask } from '../../lib/chats';
import type { ShellContext } from '../shell/AppLayout';
import { useChat } from './chatStore';
import styles from './ChatPane.module.css';
import { Composer } from './Composer';
import { Thread } from './Thread';
import { useChatController } from './useChatController';
import { useDeleteChat } from './useDeleteChat';
import { Welcome } from './Welcome';

const Trash = () => (
  <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
    <path
      d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/** The chat screen: title, the conversation (or the start screen), and the message box. Routes: / and /chat/:id. */
export function ChatPane() {
  const { me, stream } = useOutletContext<ShellContext>();
  const c = useChatController(me.devices);
  const deleteChat = useDeleteChat();
  const threadRef = useRef<HTMLDivElement>(null);

  // computers that no longer exist are forgotten; with nothing chosen, the first online one is picked
  useEffect(() => {
    useChat.getState().syncTargets(me.devices);
  }, [me.devices]);

  // keep the newest message in view: always after a question or a send, otherwise only if the person is already near the end
  const newest = c.task ? `${c.task.id}|${c.task.updated_at}|${c.task.steps?.length ?? 0}|${c.task.result ?? ''}` : '';
  const qaCount = useChat((s) => (c.task ? (s.qa[c.task.id]?.length ?? 0) : 0));
  useEffect(() => {
    const el = threadRef.current;
    if (!el || !newest) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (near || c.asking) el.scrollTop = el.scrollHeight;
  }, [newest, qaCount, c.asking]);
  const taskId = c.task?.id;
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = taskId ? el.scrollHeight : 0; // a chat opens at its end, the start screen at its top
  }, [taskId]);

  const mood = moodFor(c.task ?? runningTask(c.tasks), c.voice.listening);
  const hasDevices = me.devices.length > 0;
  const status =
    c.count !== null ? (
      <>
        {`Running in ${c.count}… `}
        <button type="button" className="link" onClick={c.cancelCountdown}>
          Cancel
        </button>
      </>
    ) : (
      c.line || (c.voice.supported ? '' : VOICE_UNSUPPORTED)
    );

  return (
    <>
      <header className={styles.top}>
        <h2 className={styles.title}>{c.chat?.title ?? 'New task'}</h2>
        <span className={styles.conn} data-state={stream} role="status" aria-live="polite">
          {stream}
        </span>
        {c.task && !c.running && (
          <button type="button" className={`ghost ${styles.del}`} onClick={() => void deleteChat(c.task?.id ?? '')}>
            <Trash />
            Delete this chat
          </button>
        )}
      </header>
      <div className={styles.thread} ref={threadRef} tabIndex={-1}>
        <div className={styles.col}>
          {c.chat && c.task ? (
            <Thread
              chat={c.chat}
              asking={c.asking}
              onAnswer={(id, body, label) => void c.answer(id, body, label)}
              onDecide={(ok) => void c.decide(ok)}
            />
          ) : c.routeId && c.loading ? null : (
            <Welcome devices={me.devices} mood={mood} pulse={c.pulse} onExample={c.setGoal} />
          )}
        </div>
      </div>
      {hasDevices && (
        <Composer
          goal={c.goal}
          onGoal={c.setGoal}
          onTyped={c.cancelCountdown}
          devices={me.devices}
          running={c.running}
          sending={c.sending}
          onSend={() => void c.run(c.goal)}
          onStop={() => void c.cancel()}
          mic={c.voice}
          status={status}
        />
      )}
    </>
  );
}
