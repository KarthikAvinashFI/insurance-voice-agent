"""State-machine tests. These are the guarantees the prompt only promises in words."""

from __future__ import annotations

import pytest

from insurance_voice_agent.state import GuardError, PolicyState


def _state(**kwargs) -> PolicyState:
    return PolicyState(caller_ani="+14155550201", session_id="test", **kwargs)


def _verified() -> PolicyState:
    state = _state()
    state.set_identity(
        {
            "policyholder_id": "ph_dana",
            "first_name": "Dana",
            "policy_id": "pol_dana",
            "policy_number": "MM-4471902",
            "policy_status": "active",
        }
    )
    state.mark_verified(
        {
            "verified": True,
            "policy_id": "pol_dana",
            "policy_number": "MM-4471902",
            "policy_status": "active",
            "first_name": "Dana",
        }
    )
    return state


def test_caller_id_alone_is_not_verification():
    state = _state()
    state.set_identity({"policyholder_id": "ph_dana", "first_name": "Dana"})
    assert state.auth_level == "ani_matched"
    with pytest.raises(GuardError):
        state.ensure_verified()


def test_unrecognised_caller_is_anonymous():
    state = _state()
    state.set_identity({"policyholder_id": None})
    assert state.auth_level == "anonymous"


def test_failed_verification_does_not_grant_access():
    state = _state()
    with pytest.raises(GuardError):
        state.mark_verified({"verified": False, "reason": "identity_factor_mismatch"})
    assert state.auth_level == "anonymous"


def test_verification_unlocks_disclosure():
    state = _verified()
    assert state.auth_level == "identity_verified"
    state.ensure_verified()  # does not raise


def test_loss_details_cannot_be_drafted_before_verification():
    state = _state()
    with pytest.raises(GuardError):
        state.draft_loss(loss_type="collision")


def test_fnol_accumulates_across_turns():
    state = _verified()
    state.draft_loss(loss_type="collision")
    state.draft_loss(loss_date="2026-08-14")
    assert state.missing_loss_fields() == ["loss_location", "description"]
    state.draft_loss(loss_location="Cesar Chavez Street", description="Rear-ended")
    assert state.missing_loss_fields() == []


def test_cannot_prepare_filing_while_details_are_missing():
    state = _verified()
    state.draft_loss(loss_type="collision")
    with pytest.raises(GuardError):
        state.prepare_fnol()


def test_filing_requires_explicit_confirmation():
    state = _verified()
    state.draft_loss(
        loss_type="collision",
        loss_date="2026-08-14",
        loss_location="Cesar Chavez Street",
        description="Rear-ended at a stop light",
    )
    token, summary = state.prepare_fnol()
    assert "collision" in summary
    with pytest.raises(GuardError):
        state.authorize_fnol(token, caller_explicitly_confirmed=False)


def test_filing_rejects_a_stale_token():
    state = _verified()
    state.draft_loss(
        loss_type="collision",
        loss_date="2026-08-14",
        loss_location="Cesar Chavez Street",
        description="Rear-ended",
    )
    token, _ = state.prepare_fnol()
    # The caller corrects a detail after the read-back.
    state.draft_loss(description="Rear-ended while stationary")
    with pytest.raises(GuardError):
        state.authorize_fnol(token, caller_explicitly_confirmed=True)


def test_successful_filing_returns_the_confirmed_snapshot():
    state = _verified()
    state.draft_loss(
        loss_type="glass",
        loss_date="2026-06-21",
        loss_location="Interstate 80",
        description="Stone chip",
    )
    token, _ = state.prepare_fnol()
    snapshot = state.authorize_fnol(token, caller_explicitly_confirmed=True)
    assert snapshot["loss_type"] == "glass"
    assert state.fnol_token is None  # token is single use


def test_lapsed_policy_cannot_file_a_claim():
    state = _verified()
    state.policy_status = "lapsed"
    with pytest.raises(GuardError):
        state.draft_loss(loss_type="collision")


def test_cancellation_requires_explicit_confirmation():
    state = _verified()
    token, summary = state.prepare_cancellation()
    assert "MM-4471902" in summary
    with pytest.raises(GuardError):
        state.authorize_cancellation(token, caller_explicitly_confirmed=False)
    state.authorize_cancellation(token, caller_explicitly_confirmed=True)


def test_cancellation_rejects_a_foreign_token():
    state = _verified()
    state.prepare_cancellation()
    with pytest.raises(GuardError):
        state.authorize_cancellation("not-the-token", caller_explicitly_confirmed=True)
