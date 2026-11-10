import { useQueryClient } from '@tanstack/react-query';
import { useNavigate, useMatch } from 'react-router-dom';
import { chatKeys, deleteChat } from '../../api/chat';
import type { TaskView } from '../../api/types';
import { toast } from '../../components/toast';
import { chatOf } from '../../lib/chats';
import { useChat } from './chatStore';

/** Delete a chat after asking. The server refuses (409) while it is still running; its message is shown as is. */
export function useDeleteChat(): (taskId: string) => Promise<void> {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const open = useMatch('/chat/:id')?.params.id ?? null;

  return async (taskId) => {
    if (!window.confirm('Delete this chat? This cannot be undone.')) return;
    const { forget } = useChat.getState();
    try {
      const r = await deleteChat(taskId);
      const ids = r.ids ?? [];
      const before = qc.getQueryData<TaskView[]>(chatKeys.tasks);
      const wasOpen = open ? chatOf(before, open) : null;
      qc.setQueryData<TaskView[]>(chatKeys.tasks, (old) => (old ?? []).filter((t) => !ids.includes(t.id)));
      forget(ids);
      if (wasOpen && wasOpen.tasks.some((t) => ids.includes(t.id))) void navigate('/'); // the open chat is gone: back to the start screen
      toast('Chat deleted.');
    } catch (e) {
      toast(e instanceof Error && e.message ? e.message : 'Could not delete the chat.', 8000);
    }
  };
}
