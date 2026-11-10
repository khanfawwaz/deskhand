/**
 * Server calls behind the "Connect your computer" and Settings dialogs: computers, other signed-in devices, saved
 * preferences, learned notes, usage, and the person's own data (export, delete). Reply shapes mirror client_api.py.
 */
import { useQuery } from '@tanstack/react-query';
import { api, ApiError, errorMessage } from './client';
import { useSession } from '../state/session';

// ------------------------------------------------------------------ reply shapes
export interface AddDeviceReply {
  code: string;
  /** seconds the one-time code works for (15 minutes) */
  expires_in: number;
  /** the one line to paste into PowerShell on the computer */
  command: string;
}

export interface WorkspaceInfo {
  id: string;
  name: string;
  max_tasks_per_day: number;
  tasks_24h: number;
}

export interface Member {
  client_id: string;
  name: string;
  last_seen: number | null;
  /** true for the device this page is running on */
  you: boolean;
}

export interface PairCodeReply {
  code: string;
  expires_in: number;
}

export interface Usage {
  tasks_24h: number;
  max_tasks_per_day: number;
  model_calls_today: number;
  tokens_today: number;
  /** 0 = no limit */
  token_limit: number;
  queue?: { waiting: number; running: number };
  paused_for_budget?: boolean;
}

export interface SavedPreference {
  id: string;
  text: string;
  created_at: number;
}

export interface SkillSummary {
  id: string;
  title: string | null;
  kind?: string | null;
  runs: { ok?: number; failed?: number };
  version?: number;
  updated_at?: number | null;
}

export interface SkillDetail extends SkillSummary {
  /** the notes as plain text */
  text: string;
  pitfalls?: (string | null)[];
}

export interface DeleteChatsReply {
  /** how many tasks were removed */
  deleted: number;
  chats: number;
}

// ------------------------------------------------------------------ query keys and hooks
export const settingsKeys = {
  connectCommand: ['connect-command'] as const,
  workspace: ['workspace'] as const,
  members: ['members'] as const,
  usage: ['usage'] as const,
  preferences: ['preferences'] as const,
  skills: ['skills'] as const,
};

/** Settings shows what is true right now: every box asks again each time the dialog opens. */
const fresh = { refetchOnMount: 'always', staleTime: 0 } as const;

export const useWorkspace = () => useQuery({ queryKey: settingsKeys.workspace, queryFn: () => api<WorkspaceInfo>('/v1/workspace'), ...fresh });
export const useMembers = () => useQuery({ queryKey: settingsKeys.members, queryFn: () => api<Member[]>('/v1/members'), ...fresh });
export const useUsage = () => useQuery({ queryKey: settingsKeys.usage, queryFn: () => api<Usage>('/v1/usage'), ...fresh });
export const usePreferences = () =>
  useQuery({ queryKey: settingsKeys.preferences, queryFn: () => api<SavedPreference[]>('/v1/preferences'), ...fresh });
export const useSkills = () => useQuery({ queryKey: settingsKeys.skills, queryFn: () => api<SkillSummary[]>('/v1/skills'), ...fresh });

// ------------------------------------------------------------------ actions
/** A one-time install command for a new computer. It works once, for 15 minutes. */
export const addDevice = () => api<AddDeviceReply>('/v1/add-device', { method: 'POST' });
export const removeDevice = (deviceId: string) => api<{ revoked: string }>(`/v1/devices/${encodeURIComponent(deviceId)}`, { method: 'DELETE' });

export const createPairCode = () => api<PairCodeReply>('/v1/pair-code', { method: 'POST' });
export const removeMember = (clientId: string) => api<{ revoked: string }>(`/v1/members/${encodeURIComponent(clientId)}`, { method: 'DELETE' });

export const deletePreference = (id: string) => api<{ forgotten: string }>(`/v1/preferences/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const getSkill = (id: string) => api<SkillDetail>(`/v1/skills/${encodeURIComponent(id)}`);
export const forgetSkill = (id: string) => api<{ forgotten: string }>(`/v1/skills/${encodeURIComponent(id)}`, { method: 'DELETE' });

/** Delete every chat that has nothing running (running ones are kept). */
export const deleteAllChats = () => api<DeleteChatsReply>('/v1/chats', { method: 'DELETE' });

/** The phrase is checked the same way on the server: case and extra spaces do not matter. */
export const DELETE_PHRASE = 'delete my data';
export const normalizePhrase = (typed: string) => typed.toLowerCase().split(/\s+/).filter(Boolean).join(' ');
export const deleteAccount = (typed: string) =>
  api<{ deleted: boolean }>('/v1/delete-account', { method: 'POST', body: { confirm: normalizePhrase(typed) } });

export const emergencyStop = () => api<{ stopped: boolean }>('/v1/estop', { method: 'POST' });
/** Tell the server to forget this device's sign-in. */
export const unpair = () => api<{ unpaired: boolean }>('/v1/unpair', { method: 'POST' });

/** Everything stored about the person, as a file. Not JSON-parsed here: it is handed to the browser as a download. */
export async function fetchExport(): Promise<Blob> {
  const { token, signOut } = useSession.getState();
  let res: Response;
  try {
    res = await fetch('/v1/export', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new ApiError('Could not reach Deskhand. Check your connection and try again.', 0);
  }
  if (res.status === 401 && token) {
    signOut('You were signed out of this device. Please sign in again.');
    throw new ApiError('unauthorized', 401);
  }
  if (!res.ok) {
    const data: unknown = await res.json().catch(() => ({}));
    throw new ApiError(errorMessage(data, 'could not download your data'), res.status);
  }
  return res.blob();
}
