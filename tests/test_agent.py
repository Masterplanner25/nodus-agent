"""nodus-agent tests."""
import pytest

from nodus_agent import (
    AgentExecutor, AgentRun, AgentStatus, CapabilityToken,
    DuplicateSubmissionGuard, GuardrailViolation, InMemoryAgentRunStore,
    LocalPlanner, LLMPlanner, check_risk_policy, mint_token, validate_token,
)


# ── AgentRun state machine ────────────────────────────────────────────────────

def test_run_defaults():
    r = AgentRun(user_id="u1", objective="test")
    assert r.status == AgentStatus.PENDING_APPROVAL
    assert r.is_terminal is False
    assert r.id


def test_run_transition_approved():
    r = AgentRun(user_id="u1", objective="test")
    r.transition(AgentStatus.APPROVED)
    assert r.status == AgentStatus.APPROVED
    assert r.approved_at is not None


def test_run_transition_completed():
    r = AgentRun(user_id="u1", objective="test")
    r.transition(AgentStatus.COMPLETED)
    assert r.is_terminal is True
    assert r.completed_at is not None


def test_run_objective_preview_truncated():
    r = AgentRun(objective="x" * 100)
    assert len(r.objective_preview) <= 83


def test_store_save_and_get():
    store = InMemoryAgentRunStore()
    r = AgentRun(user_id="u1", objective="t")
    store.save(r)
    assert store.get(r.id) is r


def test_store_list_by_user():
    store = InMemoryAgentRunStore()
    store.save(AgentRun(user_id="u1", objective="a"))
    store.save(AgentRun(user_id="u1", objective="b"))
    store.save(AgentRun(user_id="u2", objective="c"))
    assert len(store.list_by_user("u1")) == 2


# ── CapabilityToken ───────────────────────────────────────────────────────────

def test_mint_and_validate():
    token = mint_token("run-1", "u1", granted_tools=["memory.read"],
                       allowed_capabilities=["memory.read"])
    result = validate_token(token.to_dict(), "run-1", "u1")
    assert result["ok"] is True
    assert "memory.read" in result["allowed_capabilities"]
    assert "memory.read" in result["granted_tools"]


def test_validate_wrong_run_id():
    token = mint_token("run-1", "u1")
    result = validate_token(token.to_dict(), "run-2", "u1")
    assert result["ok"] is False
    assert "run_id" in result["error"]


def test_validate_wrong_user():
    token = mint_token("run-1", "u1")
    result = validate_token(token.to_dict(), "run-1", "u2")
    assert result["ok"] is False


def test_validate_tampered_token():
    token = mint_token("run-1", "u1")
    d = token.to_dict()
    d["granted_tools"] = ["admin.everything"]  # tamper
    result = validate_token(d, "run-1", "u1")
    assert result["ok"] is False
    assert "signature" in result["error"]


def test_token_is_expired():
    from datetime import datetime, timezone, timedelta
    token = mint_token("run-1", "u1", ttl_hours=0)
    token.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    result = validate_token(token.to_dict(), "run-1", "u1")
    assert result["ok"] is False


# ── LocalPlanner ──────────────────────────────────────────────────────────────

def test_local_planner_empty_tools():
    plan = LocalPlanner().plan("do something", [], {})
    assert "steps" in plan
    assert len(plan["steps"]) == 1   # fallback noop step


def test_local_planner_mentions_tool():
    tools = [{"name": "memory.read", "description": "Read memory"}]
    plan = LocalPlanner().plan("please do a memory.read of the data", tools, {})
    assert any(s["tool"] == "memory.read" for s in plan["steps"])


def test_local_planner_risk_is_low():
    plan = LocalPlanner().plan("test", [], {})
    assert plan.get("risk") == "low"


# ── check_risk_policy ─────────────────────────────────────────────────────────

def test_check_risk_low_passes():
    check_risk_policy({"risk": "low"})   # should not raise


def test_check_risk_high_raises():
    with pytest.raises(GuardrailViolation):
        check_risk_policy({"risk": "high"}, allowed_risk_levels=("low",))


def test_check_risk_missing_defaults_low():
    check_risk_policy({})   # should not raise (defaults to low)


# ── DuplicateSubmissionGuard ──────────────────────────────────────────────────

def test_dedup_allows_first():
    guard = DuplicateSubmissionGuard(window_seconds=300)
    guard.check("u1", "do the thing")   # should not raise


def test_dedup_blocks_duplicate():
    guard = DuplicateSubmissionGuard(window_seconds=300)
    guard.check("u1", "same obj")
    guard.register("u1", "same obj", "run-1")
    with pytest.raises(GuardrailViolation) as exc_info:
        guard.check("u1", "same obj")
    assert exc_info.value.existing_run_id == "run-1"


def test_dedup_allows_after_release():
    guard = DuplicateSubmissionGuard(window_seconds=300)
    guard.check("u1", "obj")
    guard.register("u1", "obj", "run-1")
    guard.release("u1", "obj")
    guard.check("u1", "obj")   # should not raise


def test_dedup_different_users_independent():
    guard = DuplicateSubmissionGuard(window_seconds=300)
    guard.register("u1", "same", "run-1")
    guard.check("u2", "same")   # different user — should not raise


# ── AgentExecutor ─────────────────────────────────────────────────────────────

class _MockTool:
    """Minimal duck-typed ToolDefinition for testing."""
    def __init__(self, name, handler):
        self.name = name
        self.description = f"Tool {name}"
        self.input_schema = {"type": "object", "properties": {}}
        self.handler = handler
        self.deprecated = False


class _MockRegistry:
    """Minimal duck-typed ToolRegistry for testing (no nodus-mcp import needed)."""
    def __init__(self):
        self._tools = {}
    def register(self, tool): self._tools[tool.name] = tool
    def get(self, name): return self._tools.get(name)
    def list(self, **_): return list(self._tools.values())


def _make_executor(auto_tools=None):
    """Build a test executor with optional mock tool registry."""
    registry = _MockRegistry()
    for name, handler in (auto_tools or {}).items():
        registry.register(_MockTool(name, handler))
    return AgentExecutor(tool_registry=registry)


def test_executor_submit_creates_run():
    ex = _make_executor()
    run = ex.submit("Write a report", "u1")
    assert run.status == AgentStatus.PENDING_APPROVAL
    assert run.user_id == "u1"


def test_executor_submit_auto_approve():
    ex = _make_executor()
    run = ex.submit("Test", "u1", auto_approve=True)
    assert run.status == AgentStatus.APPROVED


def test_executor_approve():
    ex = _make_executor()
    run = ex.submit("Test", "u1")
    approved = ex.approve(run.id)
    assert approved.status == AgentStatus.APPROVED


def test_executor_execute_completes():
    ex = _make_executor()
    run = ex.submit("Test execution", "u1", auto_approve=True)
    result = ex.execute(run.id)
    assert result["steps_completed"] >= 0
    stored = ex._run_store.get(run.id)
    assert stored.status == AgentStatus.COMPLETED


def test_executor_execute_with_tool():
    called = []
    ex = _make_executor({"noop": lambda args: called.append(args) or {"ok": True}})
    # LocalPlanner will find "noop" if mentioned in objective
    run = ex.submit("Call noop tool", "u1", auto_approve=True)
    result = ex.execute(run.id)
    # noop is mentioned → planner should include it
    assert ex._run_store.get(run.id).status == AgentStatus.COMPLETED


def test_executor_duplicate_raises():
    ex = AgentExecutor(dedup_guard=DuplicateSubmissionGuard(window_seconds=300))
    ex.submit("Same objective", "u1")
    with pytest.raises(GuardrailViolation):
        ex.submit("Same objective", "u1")


def test_executor_approve_wrong_status_raises():
    ex = _make_executor()
    run = ex.submit("Test", "u1", auto_approve=True)
    ex.execute(run.id)   # completes run
    with pytest.raises(ValueError):
        ex.approve(run.id)   # can't approve a completed run
