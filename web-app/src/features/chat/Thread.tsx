import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { fetchTask, putTasks } from '../../api/chat';
import type { TaskView } from '../../api/types';
import type { Chat } from '../../lib/chats';
import { useChat } from './chatStore';
import { QuestionCard } from './QuestionCard';
import { Turn } from './Turn';
import styles from './Thread.module.css';

interface Props {
  chat: Chat;
  /** the question this chat is waiting on, if it is the open chat's newest task */
  asking: boolean;
  onAnswer: (taskId: string, body: { choice?: string; text?: string }, label: string) => void;
  onDecide: (approved: boolean) => void;
}

/**
 * The open chat: its earlier turns, then the newest one live. The task list has no steps, so a turn whose steps we
 * do not hold yet is fetched once and merged into the cache.
 */
export function Thread({ chat, asking, onAnswer, onDecide }: Props) {
  const qc = useQueryClient();
  const qa = useChat((s) => s.qa);
  const latest = chat.latest;
  const earlier = chat.tasks.filter((t) => t.id !== latest.id && t.created_at <= latest.created_at);
  const asked = useRef(new Set<string>());

  const missing = [latest, ...earlier]
    .filter((t) => !t.steps)
    .map((t) => t.id)
    .join(',');
  useEffect(() => {
    for (const id of missing ? missing.split(',') : []) {
      if (asked.current.has(id)) continue;
      asked.current.add(id);
      fetchTask(id)
        .then((full: TaskView) => putTasks(qc, [full]))
        .catch(() => {
          /* gone */
        });
    }
  }, [missing, qc]);

  return (
    <>
      {earlier.length > 0 && (
        <div className={styles.earlier} data-testid="earlier">
          {earlier.map((t) => (
            <Turn key={t.id} task={t} qa={qa[t.id] ?? []} />
          ))}
        </div>
      )}
      <Turn task={latest} live qa={qa[latest.id] ?? []}>
        {asking && latest.pending_approval ? (
          <QuestionCard key={latest.id + JSON.stringify(latest.pending_approval)} task={latest} onAnswer={onAnswer} onDecide={onDecide} />
        ) : null}
      </Turn>
    </>
  );
}
