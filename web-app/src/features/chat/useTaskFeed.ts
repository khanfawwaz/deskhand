import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useMatch } from 'react-router-dom';
import { chatKeys, putTasks } from '../../api/chat';
import type { TaskView } from '../../api/types';
import { useEventStream } from '../../hooks/useEventStream';
import { approvalText, chatOf, statusLabel } from '../../lib/chats';
import { notify, readPrefs, speak } from './prefs';

/**
 * The live feed of task changes: every 'task' event is merged into the cached list (so every screen that reads it
 * redraws), a question raises a notification, and a finished task in the open chat can be read aloud. After a lost
 * connection the list is fetched again, so nothing that happened meanwhile is missed.
 */
export function useTaskFeed(): void {
  const qc = useQueryClient();
  const open = useMatch('/chat/:id')?.params.id ?? null;
  const seenTerminal = useRef(new Set<string>());
  const wasOffline = useRef(false);

  const status = useEventStream({
    onEvent: (e) => {
      if (e.event !== 'task' || !e.data || typeof e.data !== 'object') return;
      const t = e.data as TaskView;
      const list = qc.getQueryData<TaskView[]>(chatKeys.tasks);
      const before = list?.find((x) => x.id === t.id);
      putTasks(qc, [t]);

      if (t.pending_approval && !before?.pending_approval) notify('Deskhand needs your OK', approvalText(t.pending_approval));
      if (t.terminal && !seenTerminal.current.has(t.id) && !before?.terminal) {
        seenTerminal.current.add(t.id);
        const now = qc.getQueryData<TaskView[]>(chatKeys.tasks);
        const isOpen = open ? chatOf(now, open)?.latest.id === t.id : false;
        if (isOpen) {
          if (readPrefs().speak && t.result) speak(t.result);
          if (document.hidden) notify('Deskhand: ' + statusLabel(t.status), (t.result || t.error || t.goal).slice(0, 120));
        }
      }
    },
  });

  useEffect(() => {
    if (status !== 'online') {
      wasOffline.current = true;
      return;
    }
    if (wasOffline.current) {
      wasOffline.current = false;
      void qc.invalidateQueries({ queryKey: chatKeys.tasks });
    }
  }, [status, qc]);
}
