/**
 * What the Deskhand server sends. The generated file (schema.d.ts) types the request bodies and the routes; most
 * responses are plain dictionaries on the server, so their shapes are written down here, next to where they are used.
 */
export interface AuthConfig {
  email_login: boolean;
  signup?: string;
  extension_url?: string;
}

export interface DeviceView {
  device_id: string;
  name: string;
  online: boolean;
  last_seen: number | null;
  /** is the Deskhand Chrome extension connected on this computer right now */
  extension: boolean;
}

export interface Me {
  client_id: string;
  name: string;
  workspace: { id: string; name: string };
  devices: DeviceView[];
  /** where to get the Chrome extension ('' until it is published) */
  extension_url: string;
}

export interface TokenResponse {
  client_id: string;
  token: string;
  name: string;
  workspace: string;
  new_workspace: boolean;
}

export interface EmailSent {
  sent: boolean;
  reason?: string;
}

/* ------------------------------------------------------------------ chat (tasks, steps, questions). Owned by features/chat. */
export type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_action'
  | 'verifying'
  | 'waiting_for_confirmation'
  | 'waiting_for_device'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** One thing Deskhand did (or is about to do) in a task. */
export interface TaskStep {
  seq?: number;
  tool: string;
  status: string;
  note?: string | null;
}

export interface ApprovalOption {
  id: string;
  label: string;
}

/** A question or a request for permission that a task is waiting on. */
export interface PendingApproval {
  tool?: string | null;
  reason?: string | null;
  question?: string | null;
  params?: Record<string, unknown> | null;
  /** 'confirm' = approve/deny, 'question' and 'access' = pick an option (or type an answer) */
  kind?: 'confirm' | 'question' | 'access' | string;
  options?: ApprovalOption[];
  allow_text?: boolean;
}

/** What the server shows about a task (client_api.py task_view). The list has no `steps`; one task and live events do. */
export interface TaskView {
  id: string;
  goal: string;
  device_id: string;
  status: TaskStatus | string;
  created_at: number;
  updated_at: number;
  result: string | null;
  error: string | null;
  terminal: boolean;
  pending_approval: PendingApproval | null;
  /** the task this one continues (same chat), if any */
  continue_from?: string | null;
  /** the id of the first task of the chat this task belongs to */
  thread?: string | null;
  steps?: TaskStep[];
}

export interface TaskSubmitted {
  task_id: string;
  device_id: string;
}

export interface ChatDeleted {
  deleted: number;
  ids: string[];
}
