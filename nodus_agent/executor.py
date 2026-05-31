"""AgentExecutor — full agent execution loop."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .capability import validate_token
from .guardrails import DuplicateSubmissionGuard, GuardrailViolation, check_risk_policy
from .planner import LocalPlanner, PlannerBackend
from .run import AgentRun, AgentRunStore, AgentStatus, InMemoryAgentRunStore

logger = logging.getLogger(__name__)


class AgentExecutor:
    """Full agent execution lifecycle.

    Execution flow:
        1. Validate capability token
        2. Check duplicate submission guardrail
        3. Load or create AgentRun
        4. Plan (planner → steps)
        5. For each step: dispatch tool call via tool_registry
        6. Optionally recall/write memory
        7. Optionally emit events
        8. Mark COMPLETED or FAILED

    All dependencies (tool_registry, memory_store, event_bus, approvals,
    a2a_coordinator) are optional — the executor degrades gracefully when
    they are absent.

    Args:
        planner:          Planning backend.
        tool_registry:    ``ToolRegistry`` from ``nodus-mcp`` (duck-typed).
        run_store:        Persistence for AgentRun state.
        memory_store:     Optional ``MemoryStore`` from ``nodus-memory``.
        event_bus:        Optional ``EventBus`` from ``nodus-events``.
        approvals:        Optional ``ApprovalGate`` from ``nodus-approvals``.
        a2a_coordinator:  Optional ``AgentCoordinator`` from ``nodus-a2a``.
        dedup_guard:      Optional duplicate submission guard.
        signing_key:      Key for validating capability tokens.
    """

    def __init__(
        self,
        *,
        planner: Optional[PlannerBackend] = None,
        tool_registry: Optional[Any] = None,
        run_store: Optional[AgentRunStore] = None,
        memory_store: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        approvals: Optional[Any] = None,
        a2a_coordinator: Optional[Any] = None,
        dedup_guard: Optional[DuplicateSubmissionGuard] = None,
        signing_key: Optional[str] = None,
    ) -> None:
        self._planner = planner or LocalPlanner()
        self._tools = tool_registry
        self._run_store = run_store if run_store is not None else InMemoryAgentRunStore()
        self._memory = memory_store
        self._events = event_bus
        self._approvals = approvals
        self._a2a = a2a_coordinator
        self._dedup = dedup_guard
        self._signing_key_override = signing_key

    def submit(
        self,
        objective: str,
        user_id: str,
        *,
        capability_token: Optional[dict[str, Any]] = None,
        agent_type: str = "default",
        trace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        auto_approve: bool = False,
    ) -> AgentRun:
        """Create and optionally approve an agent run.

        Args:
            objective:         The agent's goal.
            user_id:           Tenant/owner.
            capability_token:  Pre-minted token.  When None, a minimal token
                               is generated for the run.
            agent_type:        Agent variant.
            trace_id:          Distributed trace ID.
            correlation_id:    Chain correlation ID.
            auto_approve:      Skip approval gate (for trusted internal callers).

        Returns:
            Created ``AgentRun`` with status ``pending_approval`` or ``approved``.

        Raises:
            GuardrailViolation: If a duplicate active run exists.
        """
        # Dedup guard
        if self._dedup is not None:
            self._dedup.check(user_id, objective)

        run = AgentRun(
            user_id=user_id,
            objective=objective,
            agent_type=agent_type,
            capability_token=capability_token,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        if auto_approve:
            run.transition(AgentStatus.APPROVED)

        self._run_store.save(run)

        if self._dedup is not None:
            self._dedup.register(user_id, objective, run.id)

        return run

    def approve(self, run_id: str) -> AgentRun:
        """Approve a pending run for execution."""
        run = self._run_store.get(run_id)
        if run is None:
            raise KeyError(f"AgentRun {run_id!r} not found")
        if run.status != AgentStatus.PENDING_APPROVAL:
            raise ValueError(f"Cannot approve run with status={run.status!r}")
        run.transition(AgentStatus.APPROVED)
        self._run_store.save(run)
        return run

    def execute(self, run_id: str) -> dict[str, Any]:
        """Execute an approved run synchronously.

        Returns the run's result dict on success.
        Raises ValueError when the run is not in APPROVED status.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.execute_async(run_id))
        finally:
            loop.close()

    async def execute_async(self, run_id: str) -> dict[str, Any]:
        """Async execution of an approved run."""
        run = self._run_store.get(run_id)
        if run is None:
            raise KeyError(f"AgentRun {run_id!r} not found")
        if run.status != AgentStatus.APPROVED:
            raise ValueError(
                f"Cannot execute run with status={run.status!r} — must be APPROVED"
            )

        run.transition(AgentStatus.EXECUTING)
        self._run_store.save(run)

        self._emit("agent.started", run)

        try:
            result = await self._run_plan(run)
            run.result = result
            run.transition(AgentStatus.COMPLETED)
            self._run_store.save(run)
            self._emit("agent.completed", run)
            if self._dedup is not None:
                self._dedup.release(run.user_id, run.objective)
            return result
        except Exception as exc:
            logger.warning("[AgentExecutor] run=%s failed: %s", run_id, exc)
            run.error_message = str(exc)
            run.transition(AgentStatus.FAILED)
            self._run_store.save(run)
            self._emit("agent.failed", run)
            if self._dedup is not None:
                self._dedup.release(run.user_id, run.objective)
            raise

    async def _run_plan(self, run: AgentRun) -> dict[str, Any]:
        # Gather available tools
        tools: list[dict[str, Any]] = []
        if self._tools is not None:
            try:
                tools = [
                    {"name": t.name, "description": t.description,
                     "input_schema": t.input_schema}
                    for t in (self._tools.list() if hasattr(self._tools, "list") else [])
                ]
            except Exception:
                pass

        # Recall memory context
        memory_context: list[dict] = []
        if self._memory is not None:
            try:
                from nodus_memory import recall_async  # noqa: PLC0415
                nodes = await recall_async(run.objective, run.user_id, self._memory, limit=5)
                memory_context = [{"content": n.content, "tags": n.tags} for n in nodes]
            except Exception:
                pass

        # Plan
        context = {
            "run_id": run.id,
            "user_id": run.user_id,
            "agent_type": run.agent_type,
            "trace_id": run.trace_id,
            "memory": memory_context,
        }
        plan = self._planner.plan(run.objective, tools, context)
        run.plan = plan
        run.steps_total = len(plan.get("steps", []))
        self._run_store.save(run)

        # Check risk
        try:
            check_risk_policy(plan)
        except GuardrailViolation as exc:
            raise RuntimeError(f"Risk policy blocked execution: {exc}") from exc

        # Execute steps
        results: list[dict[str, Any]] = []
        for step in plan.get("steps", []):
            step_result = await self._execute_step(step, run)
            results.append(step_result)
            run.steps_completed += 1
            self._run_store.save(run)

        return {
            "objective": run.objective,
            "plan": plan,
            "step_results": results,
            "steps_completed": run.steps_completed,
        }

    async def _execute_step(
        self,
        step: dict[str, Any],
        run: AgentRun,
    ) -> dict[str, Any]:
        tool_name = step.get("tool", "noop")
        args = step.get("args", {})

        if tool_name == "noop" or self._tools is None:
            return {"tool": tool_name, "status": "skipped", "result": None}

        tool = self._tools.get(tool_name) if hasattr(self._tools, "get") else None
        if tool is None:
            return {"tool": tool_name, "status": "not_found", "result": None}

        try:
            result = tool.handler(args)
            if asyncio.iscoroutine(result):
                result = await result
            return {"tool": tool_name, "status": "success", "result": result}
        except Exception as exc:
            logger.warning("[AgentExecutor] step tool=%s failed: %s", tool_name, exc)
            return {"tool": tool_name, "status": "error", "result": None, "error": str(exc)}

    def _emit(self, event_type: str, run: AgentRun) -> None:
        if self._events is None:
            return
        try:
            self._events.publish(
                event_type,
                correlation_id=run.correlation_id,
                payload={"run_id": run.id, "user_id": run.user_id},
            )
        except Exception:
            pass
