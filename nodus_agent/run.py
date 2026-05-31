"""AgentRun state machine and store."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore[assignment]


class AgentStatus:
    PENDING_APPROVAL = "pending_approval"
    APPROVED         = "approved"
    EXECUTING        = "executing"
    COMPLETED        = "completed"
    FAILED           = "failed"
    TERMINAL = (COMPLETED, FAILED)
    ACTIVE   = (PENDING_APPROVAL, APPROVED, EXECUTING)


@dataclass
class AgentRun:
    """Persistent state for one agent execution.

    Attributes
    ----------
    id:               Unique run ID.
    user_id:          Tenant/owner.
    objective:        The goal text given to the agent.
    agent_type:       Agent variant (default: ``"default"``).
    status:           Lifecycle status (see ``AgentStatus``).
    plan:             Steps produced by the planner.
    result:           Output on completion.
    error_message:    Error text on failure.
    capability_token: Scoped capability token authorising this run.
    steps_total:      Number of planned steps.
    steps_completed:  Steps finished so far.
    correlation_id:   Links related runs in a chain.
    trace_id:         Distributed trace ID.
    extra:            Arbitrary extra metadata.
    created_at:       UTC creation timestamp.
    approved_at:      UTC approval timestamp.
    started_at:       UTC execution start timestamp.
    completed_at:     UTC completion timestamp.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    objective: str = ""
    agent_type: str = "default"
    status: str = AgentStatus.PENDING_APPROVAL
    plan: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    capability_token: Optional[dict[str, Any]] = None
    steps_total: int = 0
    steps_completed: int = 0
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in AgentStatus.TERMINAL

    def transition(self, new_status: str) -> None:
        self.status = new_status
        now = datetime.now(timezone.utc)
        if new_status == AgentStatus.APPROVED and self.approved_at is None:
            self.approved_at = now
        elif new_status == AgentStatus.EXECUTING and self.started_at is None:
            self.started_at = now
        elif new_status in AgentStatus.TERMINAL:
            self.completed_at = now

    @property
    def objective_preview(self) -> str:
        return self.objective[:80] + "…" if len(self.objective) > 80 else self.objective


@runtime_checkable
class AgentRunStore(Protocol):
    def save(self, run: AgentRun) -> None: ...
    def get(self, run_id: str) -> Optional[AgentRun]: ...
    def list_by_user(self, user_id: str) -> list[AgentRun]: ...
    def delete(self, run_id: str) -> bool: ...


class InMemoryAgentRunStore:
    """Thread-safe in-process agent run store."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._lock = threading.Lock()

    def save(self, run: AgentRun) -> None:
        with self._lock:
            self._runs[run.id] = run

    def get(self, run_id: str) -> Optional[AgentRun]:
        with self._lock:
            return self._runs.get(run_id)

    def list_by_user(self, user_id: str) -> list[AgentRun]:
        with self._lock:
            return [r for r in self._runs.values() if r.user_id == user_id]

    def delete(self, run_id: str) -> bool:
        with self._lock:
            return self._runs.pop(run_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)
