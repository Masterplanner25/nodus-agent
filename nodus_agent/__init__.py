"""nodus-agent — full AI agent execution lifecycle.

Run lifecycle:
    AgentStatus          — PENDING_APPROVAL → APPROVED → EXECUTING → COMPLETED/FAILED
    AgentRun             — persistent state (objective, plan, result, capability_token)
    AgentRunStore        — protocol
    InMemoryAgentRunStore — thread-safe dict-backed store

Capability tokens:
    CapabilityToken      — scoped execution token
    mint_token()         — issue a signed token for a run
    validate_token()     — verify and decode a token dict

Planning:
    PlannerBackend       — protocol: plan(objective, tools, context) → dict
    LocalPlanner         — heuristic planner (no LLM, zero deps)
    LLMPlanner           — LLM-backed planner (uses nodus-llm FailoverClient)

Guardrails:
    GuardrailViolation   — raised when a guardrail blocks execution
    DuplicateSubmissionGuard — prevent concurrent identical runs
    check_risk_policy()  — block plans that exceed allowed risk levels

Executor:
    AgentExecutor        — submit(), approve(), execute(), execute_async()
                           All deps (tool_registry, memory, events, approvals,
                           a2a_coordinator) are optional.
"""
from .capability import CapabilityToken, mint_token, validate_token
from .executor import AgentExecutor
from .guardrails import (
    DuplicateSubmissionGuard,
    GuardrailViolation,
    check_risk_policy,
)
from .planner import LLMPlanner, LocalPlanner, PlannerBackend
from .run import AgentRun, AgentRunStore, AgentStatus, InMemoryAgentRunStore

__all__ = [
    # Run lifecycle
    "AgentStatus",
    "AgentRun",
    "AgentRunStore",
    "InMemoryAgentRunStore",
    # Capability tokens
    "CapabilityToken",
    "mint_token",
    "validate_token",
    # Planning
    "PlannerBackend",
    "LocalPlanner",
    "LLMPlanner",
    # Guardrails
    "GuardrailViolation",
    "DuplicateSubmissionGuard",
    "check_risk_policy",
    # Executor
    "AgentExecutor",
]
