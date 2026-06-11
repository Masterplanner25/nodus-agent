# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.1.0] — 2026-05-30

Initial release.

### Added

- **AgentRun** — persistent run record with status state machine
  (`PENDING_APPROVAL → APPROVED → EXECUTING → COMPLETED/FAILED`).
  Fields: `id`, `user_id`, `objective`, `plan`, `result`, `capability_token`,
  `created_at`, `approved_at`, `completed_at`. `is_terminal`, `objective_preview`.
  `transition(status)` records timestamps on transition.

- **InMemoryAgentRunStore** — thread-safe dict-backed `AgentRunStore` implementation.
  `save`, `get`, `list_by_user`, `list_by_status`.

- **CapabilityToken** — HMAC-SHA256 signed token scoped to `(run_id, user_id)`.
  Fields: `run_id`, `user_id`, `granted_tools`, `allowed_capabilities`,
  `expires_at`. `to_dict()` / `from_dict()`.

- **`mint_token()`** — issue a signed `CapabilityToken`.
  **`validate_token()`** — verify signature, run_id, user_id, and expiry;
  returns `{"ok": bool, ...}`.

- **LocalPlanner** — heuristic planner with zero deps. Extracts tool names
  mentioned in the objective; falls back to a single noop step. Always sets
  `risk: "low"`.

- **LLMPlanner** — accepts any object with a `.chat(messages, max_tokens)`
  method. Falls back to `LocalPlanner` on any failure.

- **AgentExecutor** — orchestrates the full lifecycle.
  `submit(objective, user_id, auto_approve?)` → `AgentRun`.
  `approve(run_id)` → transitions to `APPROVED`.
  `execute(run_id)` → runs plan steps, returns result dict, transitions to
  `COMPLETED`/`FAILED`. `execute_async(run_id)` — async variant.
  All integrations optional: `tool_registry`, `memory_store`, `event_bus`,
  `approvals`, `a2a_coordinator`, `planner`, `run_store`, `dedup_guard`.

- **DuplicateSubmissionGuard** — blocks concurrent identical `(user_id, objective)`
  submissions within `window_seconds`. `check`, `register`, `release`.

- **`check_risk_policy()`** — raises `GuardrailViolation` if `plan["risk"]` is
  not in `allowed_risk_levels`.

- **28 tests** covering all components.

- **No required dependencies** — pure stdlib; all integrations injected.

[0.1.0]: https://github.com/Masterplanner25/nodus-agent/releases/tag/v0.1.0
