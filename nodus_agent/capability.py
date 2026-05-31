"""CapabilityToken — scoped execution tokens for agent runs."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional


_TOKEN_TTL_HOURS = 24
_SIGNING_KEY = secrets.token_hex(32)   # process-level default; override in production


@dataclass
class CapabilityToken:
    """Scoped token authorising an agent run.

    Attributes
    ----------
    run_id:               The agent run this token is valid for.
    user_id:              Token owner.
    agent_type:           Agent variant.
    execution_token:      Random UUID for this issuance.
    issued_at:            UTC issue time (ISO 8601).
    expires_at:           UTC expiry time (ISO 8601).
    granted_tools:        Tool names this token allows.
    allowed_capabilities: Syscall capabilities granted.
    token_hash:           HMAC-SHA256 of the token payload.
    """

    run_id: str
    user_id: str
    agent_type: str
    execution_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = field(
        default_factory=lambda: (
            datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)
        ).isoformat()
    )
    granted_tools: list[str] = field(default_factory=list)
    allowed_capabilities: list[str] = field(default_factory=list)
    token_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "agent_type": self.agent_type,
            "execution_token": self.execution_token,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "granted_tools": self.granted_tools,
            "allowed_capabilities": self.allowed_capabilities,
            "token_hash": self.token_hash,
        }

    @property
    def is_expired(self) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > exp
        except Exception:
            return True


def _compute_hash(payload: dict, signing_key: str) -> str:
    body = json.dumps(
        {k: v for k, v in payload.items() if k != "token_hash"},
        sort_keys=True, separators=(",", ":"),
    )
    return hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()


def mint_token(
    run_id: str,
    user_id: str,
    *,
    agent_type: str = "default",
    granted_tools: Optional[list[str]] = None,
    allowed_capabilities: Optional[list[str]] = None,
    signing_key: str = _SIGNING_KEY,
    ttl_hours: int = _TOKEN_TTL_HOURS,
) -> CapabilityToken:
    """Issue a new capability token.

    Args:
        run_id:               Agent run ID this token is bound to.
        user_id:              Token owner.
        agent_type:           Agent variant.
        granted_tools:        Tool names to grant.
        allowed_capabilities: Syscall capabilities to grant.
        signing_key:          HMAC signing key (use a stable env-var secret in prod).
        ttl_hours:            Token lifetime in hours.

    Returns:
        A signed ``CapabilityToken``.
    """
    now = datetime.now(timezone.utc)
    token = CapabilityToken(
        run_id=run_id,
        user_id=user_id,
        agent_type=agent_type,
        execution_token=str(uuid.uuid4()),
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        granted_tools=list(granted_tools or []),
        allowed_capabilities=list(allowed_capabilities or []),
    )
    token.token_hash = _compute_hash(token.to_dict(), signing_key)
    return token


def validate_token(
    token: dict,
    run_id: str,
    user_id: str,
    *,
    signing_key: str = _SIGNING_KEY,
) -> dict[str, Any]:
    """Validate a capability token dict.

    Returns:
        ``{"ok": True, "allowed_capabilities": [...], "granted_tools": [...]}``
        on success.  ``{"ok": False, "error": "..."}`` on failure.
    """
    if not isinstance(token, dict):
        return {"ok": False, "error": "token must be a dict"}

    if token.get("run_id") != run_id:
        return {"ok": False, "error": "run_id mismatch"}
    if token.get("user_id") != user_id:
        return {"ok": False, "error": "user_id mismatch"}

    # Expiry check
    try:
        exp = datetime.fromisoformat(token.get("expires_at", ""))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            return {"ok": False, "error": "token expired"}
    except Exception:
        return {"ok": False, "error": "invalid expires_at"}

    # Hash verification
    expected = _compute_hash(token, signing_key)
    if not hmac.compare_digest(expected, token.get("token_hash", "")):
        return {"ok": False, "error": "invalid token signature"}

    return {
        "ok": True,
        "allowed_capabilities": token.get("allowed_capabilities", []),
        "granted_tools": token.get("granted_tools", []),
    }
