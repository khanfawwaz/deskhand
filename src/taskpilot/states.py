"""Task state machine: explicit states, guarded transitions."""
from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_ACTION = "waiting_for_action"
    VERIFYING = "verifying"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    WAITING_FOR_DEVICE = "waiting_for_device"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


S = TaskState
TERMINAL = {S.COMPLETED, S.FAILED, S.CANCELLED}
_ACTIVE = {S.RUNNING, S.WAITING_FOR_ACTION, S.VERIFYING,
           S.WAITING_FOR_CONFIRMATION, S.WAITING_FOR_DEVICE}

ALLOWED: dict[TaskState, set[TaskState]] = {
    S.QUEUED: {S.RUNNING, S.WAITING_FOR_DEVICE, S.FAILED, S.CANCELLED},
    S.RUNNING: {S.WAITING_FOR_ACTION, S.VERIFYING, S.WAITING_FOR_CONFIRMATION,
                S.WAITING_FOR_DEVICE, S.COMPLETED, S.FAILED, S.CANCELLED},
    S.WAITING_FOR_ACTION: {S.RUNNING, S.VERIFYING, S.WAITING_FOR_CONFIRMATION,
                           S.WAITING_FOR_DEVICE, S.COMPLETED, S.FAILED, S.CANCELLED},
    S.VERIFYING: {S.RUNNING, S.WAITING_FOR_ACTION, S.WAITING_FOR_DEVICE,
                  S.COMPLETED, S.FAILED, S.CANCELLED},
    S.WAITING_FOR_CONFIRMATION: {S.RUNNING, S.WAITING_FOR_ACTION, S.FAILED, S.CANCELLED},
    S.WAITING_FOR_DEVICE: {S.RUNNING, S.WAITING_FOR_ACTION, S.VERIFYING, S.FAILED, S.CANCELLED},
    S.COMPLETED: set(), S.FAILED: set(), S.CANCELLED: set(),
}


class InvalidTransition(Exception):
    pass


def check_transition(cur: TaskState, new: TaskState) -> None:
    if new not in ALLOWED[cur]:
        raise InvalidTransition(f"{cur.value} -> {new.value} is not allowed")


# Action dispatch lifecycle. 'uncertain' = sent but outcome unknown (never blindly retried).
ACTION_STATES = {"proposed", "denied", "pending_confirmation", "dispatched",
                 "succeeded", "failed", "uncertain", "cancelled"}
