# nodus-agent

**Full AI agent execution lifecycle for Nodus AI systems.**

Provides the plan → approve → execute pipeline with HMAC-signed capability
tokens, duplicate-submission guardrails, and a pluggable planner backend.
No required external dependencies — all integrations (LLM client, memory,
events, approvals) are injected at construction time.

> **Status:** v0.1.0 — published on [PyPI](https://pypi.org/project/nodus-agent/).

---

## Install

```bash
pip install nodus-agent
```

---

## What it provides

| Component | Purpose |
|---|---|
| `AgentRun` / `InMemoryAgentRunStore` | Run state machine: `PENDING_APPROVAL → APPROVED → EXECUTING → COMPLETED/FAILED` |
| `mint_token` / `validate_token` | HMAC-SHA256 capability tokens scoped to a run and user |
| `LocalPlanner` | Heuristic planner — zero deps, matches tool names from objective text |
| `LLMPlanner` | LLM-backed planner — accepts any client with a `.chat()` method |
| `AgentExecutor` | Orchestrates submit → approve → execute; all integrations optional |
| `DuplicateSubmissionGuard` | Blocks concurrent identical submissions per user |
| `check_risk_policy` | Raises `GuardrailViolation` if plan risk exceeds allowed level |

---

## Quick start

```python
from nodus_agent import AgentExecutor, LocalPlanner, AgentStatus

executor = AgentExecutor()
run = executor.submit("Summarise last week's sales data", user_id="u1")
# run.status == AgentStatus.PENDING_APPROVAL

executor.approve(run.id)
result = executor.execute(run.id)
# result == {"steps_completed": N, "outputs": [...]}
# run.status == AgentStatus.COMPLETED
```

### Auto-approve for automated pipelines

```python
run = executor.submit("Routine cleanup", "u1", auto_approve=True)
executor.execute(run.id)
```

### With a tool registry

```python
from nodus_agent import AgentExecutor

def my_tool(args):
    return {"result": "done"}

class SimpleRegistry:
    def list(self, **_): return [{"name": "my.tool", "description": "does a thing"}]
    def get(self, name): return type("T", (), {"handler": my_tool, "deprecated": False})()

executor = AgentExecutor(tool_registry=SimpleRegistry())
```

---

## Capability tokens

```python
from nodus_agent import mint_token, validate_token

token = mint_token(
    run_id="run-abc",
    user_id="u1",
    granted_tools=["memory.read", "memory.write"],
    allowed_capabilities=["memory.read", "memory.write"],
    ttl_hours=1,
)

result = validate_token(token.to_dict(), run_id="run-abc", user_id="u1")
# {"ok": True, "granted_tools": [...], "allowed_capabilities": [...]}
```

Tokens are HMAC-SHA256 signed over the payload. Tampering with any field
causes `validate_token` to return `{"ok": False, "error": "signature mismatch"}`.

---

## LLM-backed planning

```python
from nodus_agent import AgentExecutor, LLMPlanner

# Any object with a .chat(messages, max_tokens) method
class MyLLMClient:
    def chat(self, messages, max_tokens=1024):
        # call OpenAI / Anthropic / local model
        return '{"steps": [{"tool": "memory.read", "args": {}}]}'

executor = AgentExecutor(planner=LLMPlanner(MyLLMClient()))
```

`LLMPlanner` falls back to `LocalPlanner` if the LLM call fails.

---

## Guardrails

```python
from nodus_agent import (
    DuplicateSubmissionGuard, GuardrailViolation, check_risk_policy,
)

guard = DuplicateSubmissionGuard(window_seconds=300)
guard.check(user_id, objective)          # raises GuardrailViolation if duplicate
guard.register(user_id, objective, run_id)
guard.release(user_id, objective)        # call when run completes

check_risk_policy(plan, allowed_risk_levels=("low", "medium"))
# raises GuardrailViolation if plan["risk"] not in allowed_risk_levels
```

---

## Run state machine

```
PENDING_APPROVAL → APPROVED → EXECUTING → COMPLETED
                                        ↘ FAILED
```

```python
run.is_terminal   # True for COMPLETED and FAILED
run.approved_at   # datetime | None
run.completed_at  # datetime | None
run.result        # output dict | None
```

---

## Design

- **No required dependencies.** All five modules are pure stdlib. Integrations
  (LLM client, memory store, event bus, approvals, A2A coordinator) are
  passed as optional constructor arguments.
- **Pluggable planner.** Any object implementing `plan(objective, tools, context) → dict`
  satisfies `PlannerBackend`.
- **Thread-safe.** `InMemoryAgentRunStore` and `DuplicateSubmissionGuard` use
  `threading.Lock`.

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

---

## License

MIT — see [LICENSE](LICENSE).
