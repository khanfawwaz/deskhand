import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { answerTask, approveTask, cancelTask, emergencyStop, fetchTask, putTasks, submitTask, useTasks } from '../../api/chat';
import type { DeviceView, TaskView } from '../../api/types';
import { useVoice } from '../../hooks/useVoice';
import { approvalText, chatOf, continueTarget, interpretVoice, pendingTask, runningTask } from '../../lib/chats';
import { useChat } from './chatStore';
import { readPrefs } from './prefs';

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/**
 * Everything the chat screen does: which chat is open, sending a task (to one or several computers, continuing a
 * finished chat), answering, approving, stopping, and the voice input with its auto-send countdown.
 * The components only draw; the rules are here and in lib/chats.ts.
 */
export function useChatController(devices: DeviceView[]) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { id } = useParams();
  const { data: tasks, isPending } = useTasks();
  const targets = useChat((s) => s.targets);

  const chat = chatOf(tasks, id);
  const task: TaskView | null = chat?.latest ?? null; // the open chat shows its newest task as the live one
  const running = !!task && !task.terminal;

  const [goal, setGoal] = useState('');
  const [line, setLine] = useState('');
  const [sending, setSending] = useState(false);
  const [pulse, setPulse] = useState(0);
  const [count, setCount] = useState<number | null>(null);

  // ---- which chat is open
  const pending = pendingTask(tasks);
  const pendingSig = pending ? pending.id + JSON.stringify(pending.pending_approval) : '';
  const lastPending = useRef('');
  useEffect(() => {
    if (!tasks) return;
    const { bootDone, setBootDone } = useChat.getState();
    if (!bootDone) {
      setBootDone();
      // the page opened on the start screen while something is running: show it
      const active = tasks.find((t) => !t.terminal);
      if (!id && active) void navigate(`/chat/${active.thread || active.id}`, { replace: true });
      lastPending.current = pendingSig;
      return;
    }
    // a new question in another chat: bring it into view, like a chat reply
    if (pending && pendingSig !== lastPending.current && chatOf(tasks, pending.id)?.thread !== chat?.thread) {
      void navigate(`/chat/${pending.thread || pending.id}`);
    }
    lastPending.current = pendingSig;
  }, [tasks, id, pending, pendingSig, chat?.thread, navigate]);

  // ---- voice
  const handleUtterance = (text: string) => {
    const intent = interpretVoice(text, {
      awaitingApproval: !!pendingTask(tasks),
      taskRunning: !!runningTask(tasks),
    });
    switch (intent.type) {
      case 'approve':
        setLine('Approved.');
        void decide(true);
        break;
      case 'deny':
        setLine('Denied.');
        void decide(false);
        break;
      case 'cancel':
        setLine('Stopping the task.');
        void cancel();
        break;
      case 'estop':
        void estop();
        break;
      case 'ignore':
        setLine(intent.reason);
        break;
      default:
        setGoal(intent.text);
        if (readPrefs().auto) setCount(2);
        else setLine('Check the text, then press the arrow.');
    }
  };
  const voice = useVoice({ onFinal: handleUtterance, onStatus: setLine });

  // the countdown after a spoken task: 2, 1, run (Cancel stops it; typing or the mic cancels it too)
  const fire = useEffectEvent(() => void run(goal));
  useEffect(() => {
    if (count === null) return;
    const t = setTimeout(() => {
      if (count <= 1) {
        setCount(null);
        fire();
      } else setCount(count - 1);
    }, 1000);
    return () => clearTimeout(t);
  }, [count]);
  const cancelCountdown = () => {
    if (count === null) return;
    setCount(null);
    setLine('Cancelled. Edit the text or press the arrow.');
  };
  const toggleMic = () => {
    cancelCountdown();
    voice.toggle();
  };

  // ---- actions
  async function run(text: string) {
    const g = text.trim();
    if (!g) return;
    setCount(null);
    if (task && !task.terminal && !pendingTask(tasks)) {
      setLine('Deskhand is still working. Press Stop, or wait for it to finish.'); // one thing at a time in a chat
      return;
    }
    setSending(true);
    try {
      const chosen = devices.filter((d) => targets.includes(d.device_id));
      const live = chosen.filter((d) => d.online);
      const skipped = chosen.filter((d) => !d.online);
      const first = chosen[0];
      if (!live.length) {
        setLine(first ? first.name + ' is offline: open Deskhand on it first.' : 'Choose a computer to run this on.');
        return;
      }
      let firstTask: TaskView | null = null;
      const failed: string[] = [];
      const cf = continueTarget(task); // a finished chat is continued; "New task" starts a fresh one
      for (const d of live) {
        try {
          const r = await submitTask({
            goal: g,
            device_id: d.device_id,
            continue_from: cf,
          });
          const full = await fetchTask(r.task_id);
          putTasks(qc, [full]);
          firstTask ??= full;
        } catch (e) {
          failed.push(d.name + ': ' + message(e));
        }
      }
      if (firstTask) {
        setGoal('');
        setLine('');
        setPulse((p) => p + 1);
        void navigate(`/chat/${firstTask.thread || firstTask.id}`);
      }
      const notes: string[] = [];
      if (live.length > 1 && firstTask) notes.push('Sent to ' + (live.length - failed.length) + ' computers.');
      if (skipped.length) notes.push('Skipped offline: ' + skipped.map((d) => d.name).join(', ') + '.');
      if (failed.length) notes.push(failed.join(' '));
      if (notes.length) setLine(notes.join(' '));
    } catch (e) {
      setLine(message(e));
    } finally {
      setSending(false);
    }
  }

  async function answer(taskId: string, body: { choice?: string; text?: string }, label: string) {
    const t = tasks?.find((x) => x.id === taskId);
    const question = t?.pending_approval ? approvalText(t.pending_approval) : '';
    try {
      await answerTask(taskId, body);
      useChat.getState().addQa(taskId, {
        q: question,
        a: label || body.text || body.choice || '',
      });
    } catch (e) {
      setLine(message(e));
    }
  }

  async function decide(approved: boolean) {
    const t = pendingTask(tasks);
    if (!t) return;
    try {
      await approveTask(t.id, approved);
    } catch (e) {
      setLine(message(e));
    }
  }

  async function cancel() {
    const t = task ?? runningTask(tasks);
    if (!t || t.terminal) return;
    try {
      await cancelTask(t.id);
    } catch (e) {
      setLine(message(e));
    }
  }

  async function estop() {
    try {
      await emergencyStop();
      setLine('Emergency stop sent: all tasks are being stopped.');
    } catch (e) {
      setLine(message(e));
    }
  }

  return {
    tasks,
    loading: isPending,
    routeId: id,
    chat,
    task,
    running,
    asking: !!pending && !!task && pending.id === task.id,
    pulse,
    goal,
    setGoal,
    line,
    setLine,
    count,
    cancelCountdown,
    sending,
    voice: {
      supported: voice.supported,
      listening: voice.listening,
      toggle: toggleMic,
    },
    run,
    answer,
    decide,
    cancel,
  };
}
