/**
 * Server calls behind the chat: the list of tasks (chats are threads of tasks), sending a task, answering a question,
 * approving, stopping, deleting a chat. Request bodies are typed from the generated schema.
 */
import { useQuery, type QueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { components } from './schema';
import type { ChatDeleted, TaskSubmitted, TaskView } from './types';
import { mergeTasks, replaceTasks } from '../lib/chats';
import { useSession } from '../state/session';

export const chatKeys = {
  tasks: ['tasks'] as const,
};

/** How many recent tasks the list asks for: enough to rebuild the recent chats. */
export const TASK_LIMIT = 60;

/** The person's recent tasks, newest first (without steps: one task, and live events, carry them). Kept current by the event stream. */
export function useTasks() {
  const token = useSession((s) => s.token);
  return useQuery({
    queryKey: chatKeys.tasks,
    // a refetch must not wipe the steps we already hold for a task
    queryFn: async ({ client }) =>
      replaceTasks(await api<TaskView[]>(`/v1/tasks?limit=${TASK_LIMIT}`), client.getQueryData<TaskView[]>(chatKeys.tasks)),
    enabled: !!token,
    staleTime: 30_000,
  });
}

/** Put newer copies of tasks (from an event or a fetch) into the cached list. */
export function putTasks(qc: QueryClient, incoming: TaskView[]): void {
  qc.setQueryData<TaskView[]>(chatKeys.tasks, (old) => mergeTasks(old, incoming));
}

/** Refresh the list from the server, keeping the steps we already have. */
export async function reloadTasks(qc: QueryClient): Promise<void> {
  const list = await api<TaskView[]>(`/v1/tasks?limit=${TASK_LIMIT}`);
  qc.setQueryData<TaskView[]>(chatKeys.tasks, (old) => replaceTasks(list, old));
}

export const fetchTask = (id: string): Promise<TaskView> => api<TaskView>(`/v1/tasks/${encodeURIComponent(id)}`);

export interface SubmitInput {
  goal: string;
  device_id: string;
  continue_from?: string | null;
}

/** Start one task on one computer. */
export function submitTask(input: SubmitInput): Promise<TaskSubmitted> {
  // the server fills in max_actions and timeout_s
  const body: Pick<components['schemas']['taskpilot__client_api__TaskIn'], 'goal' | 'device_id' | 'continue_from'> = {
    goal: input.goal,
    device_id: input.device_id,
    ...(input.continue_from ? { continue_from: input.continue_from } : {}),
  };
  return api<TaskSubmitted>('/v1/tasks', { method: 'POST', body });
}

export function answerTask(id: string, body: components['schemas']['AnswerIn']): Promise<{ answered: boolean }> {
  return api(`/v1/tasks/${encodeURIComponent(id)}/answer`, {
    method: 'POST',
    body,
  });
}

export function approveTask(id: string, approved: boolean): Promise<unknown> {
  const body: components['schemas']['taskpilot__client_api__ApproveIn'] = {
    approved,
  };
  return api(`/v1/tasks/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
    body,
  });
}

export const cancelTask = (id: string): Promise<unknown> => api(`/v1/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' });

export const emergencyStop = (): Promise<unknown> => api('/v1/estop', { method: 'POST' });

/** Delete the whole chat that contains this task. The server answers 409 while any of it is still running. */
export const deleteChat = (id: string): Promise<ChatDeleted> =>
  api<ChatDeleted>(`/v1/chats/${encodeURIComponent(id)}`, { method: 'DELETE' });
