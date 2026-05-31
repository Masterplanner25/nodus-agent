"""PlannerBackend protocol, LocalPlanner, and LLMPlanner."""
from __future__ import annotations

import re
from typing import Any, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore[assignment]


@runtime_checkable
class PlannerBackend(Protocol):
    """Protocol for agent planning backends."""

    def plan(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate an execution plan.

        Args:
            objective: The agent's goal.
            tools:     Available tools (list of {name, description, input_schema}).
            context:   Execution context (user_id, trace_id, memory, etc.).

        Returns:
            A plan dict: ``{"steps": [{"tool": str, "args": dict}, ...], ...}``.
        """
        ...


class LocalPlanner:
    """Heuristic planner that creates a plan without calling any LLM.

    Useful for testing, low-risk automations, and situations where the
    objective maps directly to a known tool.

    Extracts tool names mentioned in the objective and creates one step per tool.
    Falls back to a single generic step when no tools are mentioned.
    """

    def plan(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tool_names = {t["name"] for t in tools if isinstance(t, dict) and "name" in t}
        mentioned = [
            name for name in tool_names
            if name.lower() in objective.lower()
        ]

        steps: list[dict[str, Any]]
        if mentioned:
            steps = [{"tool": name, "args": {}, "description": f"Execute {name}"} for name in mentioned]
        else:
            steps = [{"tool": "noop", "args": {}, "description": objective[:100]}]

        return {
            "objective": objective,
            "steps": steps,
            "planner": "local",
            "risk": "low",
        }


class LLMPlanner:
    """LLM-backed planner using the nodus-llm FailoverClient.

    Args:
        client:          A ``FailoverClient`` (or any object with ``.chat()``).
        system_prompt:   Optional custom system prompt.
        max_steps:       Maximum steps the planner may generate (default: 10).
    """

    _DEFAULT_SYSTEM = (
        "You are an AI agent planner. Given an objective and a list of available tools, "
        "produce a JSON execution plan with a 'steps' array. Each step has 'tool', 'args', "
        "and 'description' fields. Respond with JSON only, no prose."
    )

    def __init__(
        self,
        client: Any,
        *,
        system_prompt: Optional[str] = None,
        max_steps: int = 10,
    ) -> None:
        self._client = client
        self._system = system_prompt or self._DEFAULT_SYSTEM
        self._max_steps = max_steps

    def plan(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        import json  # noqa: PLC0415
        tool_list = "\n".join(
            f"- {t.get('name', '?')}: {t.get('description', '')}"
            for t in tools[:20]   # cap tool list to avoid context overflow
        )
        user_message = (
            f"Objective: {objective}\n\n"
            f"Available tools:\n{tool_list}\n\n"
            f"Produce a JSON plan with at most {self._max_steps} steps."
        )
        try:
            raw = self._client.chat(
                [
                    {"role": "system", "content": self._system},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=1024,
            )
            # Extract JSON from the response
            json_match = re.search(r"\{[\s\S]*\}", raw)
            if json_match:
                plan = json.loads(json_match.group())
                plan.setdefault("planner", "llm")
                plan.setdefault("objective", objective)
                return plan
        except Exception:
            pass
        # Fallback to local planner on any failure
        return LocalPlanner().plan(objective, tools, context)
