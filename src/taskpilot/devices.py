"""Server-side view of connected agents. Transport is abstracted so tests can loop back."""
from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from .protocol import ActionRequest, ActionResult


class DeviceUnavailable(Exception):
    """Device is not connected, or the link dropped while awaiting a reply."""


class ActionTimeout(Exception):
    """No reply within the deadline: the action's outcome is UNKNOWN."""


class DeviceConnection(ABC):
    @abstractmethod
    async def execute(self, req: ActionRequest, timeout: float) -> ActionResult: ...

    @abstractmethod
    async def query(self, action_id: str, timeout: float = 5.0) -> ActionResult | None:
        """Ask the agent whether it ever executed `action_id` (None = never seen)."""

    @abstractmethod
    async def cancel(self, task_id: str) -> None: ...

    @abstractmethod
    async def estop(self) -> None: ...


class DeviceHub:
    def __init__(self) -> None:
        self._conns: dict[str, DeviceConnection] = {}
        self._events: dict[str, asyncio.Event] = {}

    def _ev(self, device_id: str) -> asyncio.Event:
        return self._events.setdefault(device_id, asyncio.Event())

    def register(self, device_id: str, conn: DeviceConnection) -> None:
        self._conns[device_id] = conn
        self._ev(device_id).set()

    def unregister(self, device_id: str, conn: DeviceConnection | None = None) -> None:
        if conn is None or self._conns.get(device_id) is conn:
            self._conns.pop(device_id, None)
            self._ev(device_id).clear()

    def get(self, device_id: str) -> DeviceConnection | None:
        return self._conns.get(device_id)

    def online(self) -> list[str]:
        return list(self._conns)

    async def wait_for(self, device_id: str, timeout: float) -> DeviceConnection | None:
        if (c := self.get(device_id)) is not None:
            return c
        try:
            await asyncio.wait_for(self._ev(device_id).wait(), timeout)
        except asyncio.TimeoutError:
            return None
        return self.get(device_id)

    async def estop_devices(self, device_ids: list[str]) -> None:
        for did in device_ids:
            if (c := self._conns.get(did)) is not None:
                try:
                    await c.estop()
                except Exception:
                    pass

    async def estop_all(self) -> None:
        for c in list(self._conns.values()):
            try:
                await c.estop()
            except Exception:
                pass


class WebSocketConnection(DeviceConnection):
    """Adapter over any object with async `send(str)`; feed replies to `on_message`."""

    def __init__(self, send: Callable[[str], Awaitable[None]]):
        self._send = send
        self._pending: dict[str, asyncio.Future] = {}
        self.closed = False

    async def _rpc(self, msg: dict, key: str, timeout: float) -> dict:
        if self.closed:
            raise DeviceUnavailable("connection closed")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[key] = fut
        try:
            await self._send(json.dumps(msg))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ActionTimeout(f"no reply for {key} within {timeout}s") from None
        except (ConnectionError, RuntimeError, OSError) as e:
            raise DeviceUnavailable(str(e)) from e
        finally:
            self._pending.pop(key, None)

    async def execute(self, req: ActionRequest, timeout: float) -> ActionResult:
        reply = await self._rpc({"type": "action", "request": req.model_dump(mode="json")},
                                req.action_id, timeout)
        return ActionResult.model_validate(reply["result"])

    async def query(self, action_id: str, timeout: float = 5.0) -> ActionResult | None:
        qid = "q-" + uuid.uuid4().hex[:8]
        reply = await self._rpc({"type": "query", "qid": qid, "action_id": action_id}, qid, timeout)
        return ActionResult.model_validate(reply["result"]) if reply.get("result") else None

    async def cancel(self, task_id: str) -> None:
        if not self.closed:
            await self._send(json.dumps({"type": "cancel", "task_id": task_id}))

    async def estop(self) -> None:
        if not self.closed:
            await self._send(json.dumps({"type": "estop"}))

    def on_message(self, msg: dict[str, Any]) -> None:
        key = msg.get("result", {}).get("action_id") if msg.get("type") == "result" else msg.get("qid")
        if (fut := self._pending.get(key)) and not fut.done():
            fut.set_result(msg)

    def close(self) -> None:
        self.closed = True
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(DeviceUnavailable("device disconnected"))
