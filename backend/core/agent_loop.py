"""
core/agent_loop.py — JARVIS autonomous ReAct agent loop.

Flow:
  1. User gives a task
  2. LLM decides which tool to call (or returns final answer)
  3. Tool call is sent to laptop_agent.py over WebSocket
  4. Result comes back, fed into next LLM step
  5. Repeat until done (max 12 steps)

Progress is streamed to the user via progress_fn so they can
follow what JARVIS is doing in real time.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import core.llm_client as llm
from core.tools import TOOLS
from core.persona import SYSTEM_PROMPT

log = logging.getLogger("jarvis.agent")

MAX_STEPS   = 12
TOOL_TIMEOUT = 30   # seconds to wait for laptop to execute each tool

_AGENT_SYSTEM = (
    SYSTEM_PROMPT
    + "\n\n━━━ AGENT MODE ━━━\n"
    "You are now operating in Autonomous Agent Mode.\n"
    "You have tools to control the user's laptop. Use them to complete tasks.\n"
    "Think step by step. After each tool result, decide if the task is done or if more steps are needed.\n"
    "When done, give a concise final report in spoken Korean ending with ', Sir'.\n"
    "Never make up tool results — always call the tool and wait for the real output.\n"
)


class AgentLoop:
    """
    Orchestrates a multi-step tool-calling session.
    One instance per active task; destroyed when task finishes.
    """

    def __init__(
        self,
        broadcast_to_laptop: Callable[[dict], Awaitable[None]],
        progress_fn:         Callable[[str], Awaitable[None]],
    ) -> None:
        self._send_tool    = broadcast_to_laptop   # sends tool_call to laptop
        self._progress     = progress_fn            # streams progress text to user
        self._pending: dict[str, asyncio.Future] = {}

    # ── External: called by WS handler when laptop_agent sends tool_result ────

    def on_tool_result(self, tool_id: str, result: str) -> None:
        fut = self._pending.pop(tool_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    # ── Main run loop ─────────────────────────────────────────────────────────

    async def run(self, task: str) -> str:
        """Execute a task autonomously. Returns the final reply text."""
        messages: list[dict] = [{"role": "user", "content": task}]
        await self._progress(f"[JARVIS AGENT] 작업 분석 중: {task[:60]}…")

        for step in range(1, MAX_STEPS + 1):
            response = await asyncio.to_thread(
                llm.generate_with_tools,
                messages,
                TOOLS,
                _AGENT_SYSTEM,
            )

            tool_calls = response.get("tool_calls")
            content    = response.get("content", "")
            raw_msg    = response.get("raw_message")

            # No tool calls → final answer
            if not tool_calls:
                return content or "작업이 완료되었습니다, Sir."

            # Append assistant message (with tool_calls) to history
            if raw_msg:
                messages.append(raw_msg)

            # Execute each tool call sequentially
            for tc in tool_calls:
                tc_id   = tc.id
                tc_name = tc.function.name
                try:
                    tc_args = json.loads(tc.function.arguments)
                except Exception:
                    tc_args = {}

                await self._progress(
                    f"[Step {step}] {tc_name}({', '.join(f'{k}={repr(v)[:40]}' for k, v in tc_args.items())})"
                )

                result = await self._call_tool(tc_id, tc_name, tc_args)
                await self._progress(f"  └─ 결과: {result[:120]}")

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc_id,
                    "name":         tc_name,   # needed for Gemini function_response
                    "content":      result,
                })

        return "최대 실행 단계에 도달했습니다. 작업이 부분적으로 완료되었을 수 있습니다, Sir."

    async def _call_tool(self, tc_id: str, name: str, args: dict) -> str:
        """Send tool call to laptop and wait for result."""
        fut = asyncio.get_event_loop().create_future()
        self._pending[tc_id] = fut

        try:
            await self._send_tool({
                "type": "tool_call",
                "id":   tc_id,
                "name": name,
                "args": args,
            })
            return await asyncio.wait_for(fut, timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(tc_id, None)
            return f"[timeout] {name} 실행이 {TOOL_TIMEOUT}초를 초과했습니다."
        except Exception as exc:
            self._pending.pop(tc_id, None)
            return f"[error] {exc}"


# ── Module-level active agent registry ───────────────────────────────────────
# Maps session_id → AgentLoop. Allows on_tool_result to find the right loop.

_active: dict[str, AgentLoop] = {}


def get_or_create(
    session_id: str,
    broadcast_to_laptop: Callable[[dict], Awaitable[None]],
    progress_fn: Callable[[str], Awaitable[None]],
) -> AgentLoop:
    if session_id not in _active:
        _active[session_id] = AgentLoop(broadcast_to_laptop, progress_fn)
    return _active[session_id]


def remove(session_id: str) -> None:
    _active.pop(session_id, None)


def dispatch_tool_result(tool_id: str, result: str) -> bool:
    """Route a tool_result to whichever AgentLoop is waiting on it."""
    for loop in _active.values():
        if tool_id in loop._pending:
            loop.on_tool_result(tool_id, result)
            return True
    return False
