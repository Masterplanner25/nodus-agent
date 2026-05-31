"""AgentGuardrails — duplicate submission guard and risk enforcement."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class GuardrailViolation(Exception):
    """Raised when a guardrail blocks an agent submission."""

    reason: str
    existing_run_id: Optional[str] = None

    def __str__(self) -> str:
        return f"GuardrailViolation: {self.reason}"


class DuplicateSubmissionGuard:
    """Prevent concurrent duplicate agent runs for the same objective+user.

    A submission is considered a duplicate when the same ``(user_id, objective_hash)``
    combination already has an active (non-terminal) run within the dedup window.

    Args:
        window_seconds: How long to block duplicate submissions (default: 300s).
    """

    def __init__(self, window_seconds: int = 300) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._active: dict[str, tuple[str, datetime]] = {}   # key → (run_id, created_at)
        self._lock = threading.Lock()

    def _key(self, user_id: str, objective: str) -> str:
        payload = json.dumps({"user_id": user_id, "objective": objective},
                             sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def check(self, user_id: str, objective: str) -> None:
        """Raise ``GuardrailViolation`` if a duplicate active run exists."""
        key = self._key(user_id, objective)
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._active.get(key)
            if entry is not None:
                run_id, created_at = entry
                if now - created_at < self._window:
                    raise GuardrailViolation(
                        reason=f"Duplicate submission within {self._window.seconds}s window",
                        existing_run_id=run_id,
                    )

    def register(self, user_id: str, objective: str, run_id: str) -> None:
        """Register an active run (call after check() passes)."""
        key = self._key(user_id, objective)
        with self._lock:
            self._active[key] = (run_id, datetime.now(timezone.utc))

    def release(self, user_id: str, objective: str) -> None:
        """Release the dedup slot (call after run completes or fails)."""
        key = self._key(user_id, objective)
        with self._lock:
            self._active.pop(key, None)


def check_risk_policy(plan: dict, *, allowed_risk_levels: tuple[str, ...] = ("low", "medium")) -> None:
    """Raise ``GuardrailViolation`` if plan risk exceeds allowed levels."""
    risk = str(plan.get("risk", "low")).lower()
    if risk not in allowed_risk_levels:
        raise GuardrailViolation(
            reason=f"Plan risk level {risk!r} not in allowed levels {allowed_risk_levels}"
        )
