from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any


class GuardError(ValueError):
    """Raised when an action is not safe to take yet."""


# Anything on this list needs an underwriter, not a phone assistant.
UNDERWRITER_TOPICS = {
    "add_vehicle",
    "remove_vehicle",
    "add_driver",
    "remove_driver",
    "change_coverage",
    "change_deductible",
}


@dataclass
class PolicyState:
    """Tracks what has been verified and what may therefore be disclosed.

    The prompt states the rules in words; this class is what actually enforces
    them, so a scenario check about disclosure or confirmation has something
    deterministic to assert against.
    """

    caller_ani: str
    session_id: str
    auth_level: str = "anonymous"  # anonymous | ani_matched | identity_verified
    policyholder_id: str | None = None
    first_name: str | None = None
    policy_id: str | None = None
    policy_number: str | None = None
    policy_status: str = "unknown"
    balance_due: float = 0.0
    open_claim_ref: str | None = None

    # First Notice of Loss draft, built up across several turns.
    fnol: dict[str, Any] = field(default_factory=dict)
    fnol_token: str | None = None
    fnol_digest: str | None = None

    cancellation_token: str | None = None
    last_payment_link_id: str | None = None
    filed_claim_ref: str | None = None

    # ------------------------------------------------------------ identity

    def set_identity(self, identity: dict[str, Any]) -> None:
        self.policyholder_id = identity.get("policyholder_id")
        self.first_name = identity.get("first_name")
        self.policy_id = identity.get("policy_id")
        self.policy_number = identity.get("policy_number")
        self.policy_status = identity.get("policy_status") or "unknown"
        self.balance_due = float(identity.get("balance_due") or 0)
        self.open_claim_ref = identity.get("open_claim_ref")
        # Caller ID alone is recognition, never verification.
        self.auth_level = "ani_matched" if self.policyholder_id else "anonymous"

    def mark_verified(self, result: dict[str, Any]) -> None:
        if not result.get("verified"):
            raise GuardError("That did not match our records.")
        self.auth_level = "identity_verified"
        self.policy_id = result.get("policy_id") or self.policy_id
        self.policy_number = result.get("policy_number") or self.policy_number
        self.policy_status = result.get("policy_status") or self.policy_status
        self.first_name = result.get("first_name") or self.first_name

    def ensure_verified(self, what: str = "policy details") -> None:
        if self.auth_level != "identity_verified":
            raise GuardError(
                f"Verify the policy number and one identity detail before sharing {what}."
            )

    def ensure_policy(self) -> str:
        if not self.policy_id:
            raise GuardError("No policy is attached to this call yet.")
        return self.policy_id

    def ensure_active_policy(self) -> None:
        if self.policy_status != "active":
            raise GuardError(
                f"This policy is {self.policy_status.replace('_', ' ')}, "
                "so that cannot be done on this call."
            )

    # ------------------------------------------------------------ FNOL

    def draft_loss(self, **fields: Any) -> dict[str, Any]:
        """Accumulate loss details across turns; the caller rarely gives them at once."""
        self.ensure_verified("claim details")
        self.ensure_active_policy()
        for key, value in fields.items():
            if value is not None and str(value).strip():
                self.fnol[key] = value
        self._clear_fnol_confirmation()
        return dict(self.fnol)

    def missing_loss_fields(self) -> list[str]:
        required = ["loss_type", "loss_date", "loss_location", "description"]
        return [key for key in required if not self.fnol.get(key)]

    def prepare_fnol(self) -> tuple[str, str]:
        self.ensure_verified("claim details")
        self.ensure_active_policy()
        missing = self.missing_loss_fields()
        if missing:
            raise GuardError(
                "Still needed before filing: " + ", ".join(m.replace("_", " ") for m in missing)
            )
        raw = json.dumps(self.fnol, sort_keys=True, separators=(",", ":"))
        self.fnol_digest = hashlib.sha256(raw.encode()).hexdigest()
        self.fnol_token = secrets.token_urlsafe(12)
        summary = (
            f"a {self.fnol['loss_type']} loss on {self.fnol['loss_date']} "
            f"at {self.fnol['loss_location']}, described as {self.fnol['description']}"
        )
        if self.fnol.get("other_party"):
            summary += f", involving {self.fnol['other_party']}"
        return self.fnol_token, summary

    def authorize_fnol(self, token: str, caller_explicitly_confirmed: bool) -> dict[str, Any]:
        if not caller_explicitly_confirmed:
            raise GuardError("The caller must confirm the loss details before filing.")
        if not self.fnol_token or token != self.fnol_token:
            raise GuardError("That confirmation token does not match this claim draft.")
        raw = json.dumps(self.fnol, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(raw.encode()).hexdigest() != self.fnol_digest:
            raise GuardError("The loss details changed after confirmation; read them back again.")
        snapshot = dict(self.fnol)
        self._clear_fnol_confirmation()
        return snapshot

    # ------------------------------------------------------------ cancellation

    def prepare_cancellation(self) -> tuple[str, str]:
        self.ensure_verified("a cancellation")
        self.ensure_policy()
        self.cancellation_token = secrets.token_urlsafe(12)
        return (
            self.cancellation_token,
            f"cancelling policy {self.policy_number}, which ends the cover on it",
        )

    def authorize_cancellation(self, token: str, caller_explicitly_confirmed: bool) -> None:
        if not caller_explicitly_confirmed:
            raise GuardError("The caller must say yes explicitly before a cancellation.")
        if not self.cancellation_token or token != self.cancellation_token:
            raise GuardError("That cancellation token does not match this call.")
        self.cancellation_token = None

    # ------------------------------------------------------------ helpers

    def _clear_fnol_confirmation(self) -> None:
        self.fnol_token = None
        self.fnol_digest = None
