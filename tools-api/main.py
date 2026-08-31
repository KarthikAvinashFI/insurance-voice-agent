"""Tools API — the ONLY source of coverage, deductibles, claim status and money.

Every endpoint mirrors one agent tool. The agent calls these; it never decides a
deductible, invents a claim status, or quotes a settlement itself. That separation
is what makes "no invented coverage" checkable: any figure the agent speaks must
trace back to a response from here.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://meridian:meridian@postgres:5432/meridian_demo"
)
app = FastAPI(title="Meridian Mutual voice agent tools", version="1.0.0")

ADJUSTERS = [
    ("Ruth Alvarez", "+18005550111"),
    ("Peter Nkemelu", "+18005550112"),
    ("Simone Bianchi", "+18005550113"),
]


def db():
    return psycopg.connect(DSN, row_factory=dict_row)


def one(sql: str, params: tuple = ()) -> dict | None:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def many(sql: str, params: tuple = ()) -> list[dict]:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def run(sql: str, params: tuple = ()) -> None:
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        c.commit()


def _f(v) -> float | None:
    return None if v is None else float(v)


def _d(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, (date, datetime)) else str(v)


def _policy_for(policy_id: str) -> dict:
    policy = one("SELECT * FROM policies WHERE policy_id = %s", (policy_id,))
    if not policy:
        raise HTTPException(404, "policy_not_found")
    return policy


# ---------------------------------------------------------------- models


class PhoneIn(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class VerifyIn(BaseModel):
    policy_number: str
    date_of_birth: str | None = None
    zip_code: str | None = None


class PolicyIn(BaseModel):
    policy_id: str


class CoverageIn(BaseModel):
    policy_id: str
    coverage_type: str


class ClaimRefIn(BaseModel):
    claim_ref: str


class FileClaimIn(BaseModel):
    policy_id: str
    loss_type: str
    loss_date: str
    loss_location: str
    description: str
    other_party: str | None = None
    idempotency_key: str


class PayLinkIn(BaseModel):
    phone: str
    policy_id: str
    amount: float | None = None


class LinkIdIn(BaseModel):
    link_id: str


class OtpIn(BaseModel):
    phone: str
    code: str


class CancelIn(BaseModel):
    policy_id: str
    reason: str
    idempotency_key: str


class TransferIn(BaseModel):
    phone: str
    reason: str
    queue: str = "general"


# ---------------------------------------------------------------- health


@app.get("/health")
def health() -> dict:
    try:
        one("SELECT 1 AS ok")
        return {"ok": True}
    except Exception as exc:  # surfaced so the agent can say "having trouble"
        raise HTTPException(503, "db_unavailable") from exc


# ---------------------------------------------------------------- identity


@app.post("/lookup_policyholder_by_phone")
def lookup_policyholder_by_phone(body: PhoneIn) -> dict:
    ph = one("SELECT * FROM policyholders WHERE phone = %s", (body.phone,))
    if not ph:
        return {"policyholder_id": None, "first_name": None, "policy_status": "unknown"}
    policy = one(
        "SELECT * FROM policies WHERE policyholder_id = %s ORDER BY effective_date DESC LIMIT 1",
        (ph["policyholder_id"],),
    )
    open_claim = None
    if policy:
        open_claim = one(
            "SELECT claim_ref FROM claims WHERE policy_id = %s"
            " AND status NOT IN ('closed','denied','paid') ORDER BY opened_at DESC LIMIT 1",
            (policy["policy_id"],),
        )
    return {
        "policyholder_id": ph["policyholder_id"],
        "first_name": ph["first_name"],
        "last_name": ph["last_name"],
        "policy_id": policy["policy_id"] if policy else None,
        "policy_number": policy["policy_number"] if policy else None,
        "policy_status": policy["status"] if policy else "unknown",
        "balance_due": _f(policy["balance_due"]) if policy else 0.0,
        "renewal_date": _d(policy["renewal_date"]) if policy else None,
        "open_claim_ref": open_claim["claim_ref"] if open_claim else None,
    }


@app.post("/verify_identity")
def verify_identity(body: VerifyIn) -> dict:
    """Match a spoken policy number against one identity factor. Never echoes the factor."""
    policy = one(
        "SELECT p.*, h.date_of_birth, h.zip_code FROM policies p"
        " JOIN policyholders h ON h.policyholder_id = p.policyholder_id"
        " WHERE p.policy_number = %s",
        (body.policy_number.strip().upper(),),
    )
    if not policy:
        return {"verified": False, "reason": "policy_number_not_found"}
    if body.date_of_birth:
        ok = _d(policy["date_of_birth"]) == body.date_of_birth.strip()
    elif body.zip_code:
        ok = str(policy["zip_code"]).strip() == body.zip_code.strip()
    else:
        return {"verified": False, "reason": "no_identity_factor_supplied"}
    if not ok:
        return {"verified": False, "reason": "identity_factor_mismatch"}
    return {
        "verified": True,
        "policy_id": policy["policy_id"],
        "policy_number": policy["policy_number"],
        "policy_status": policy["status"],
        "first_name": one(
            "SELECT first_name FROM policyholders WHERE policyholder_id = %s",
            (policy["policyholder_id"],),
        )["first_name"],
    }


@app.post("/send_otp")
def send_otp(body: PhoneIn) -> dict:
    exists = one("SELECT 1 FROM otp_codes WHERE phone = %s", (body.phone,))
    if exists:
        run(
            "UPDATE otp_codes SET attempts_left = 3, verified = FALSE,"
            " issued_at = now() WHERE phone = %s",
            (body.phone,),
        )
    else:
        run("INSERT INTO otp_codes (phone, code) VALUES (%s, '246813')", (body.phone,))
    return {"sent": True, "expires_in_seconds": 300}


@app.post("/verify_otp")
def verify_otp(body: OtpIn) -> dict:
    row = one("SELECT * FROM otp_codes WHERE phone = %s", (body.phone,))
    if not row:
        return {"verified": False, "reason": "no_code_outstanding"}
    if row["attempts_left"] <= 0:
        return {"verified": False, "reason": "no_attempts_left"}
    if str(row["code"]) != str(body.code).strip():
        run(
            "UPDATE otp_codes SET attempts_left = attempts_left - 1 WHERE phone = %s",
            (body.phone,),
        )
        return {"verified": False, "reason": "code_mismatch"}
    run("UPDATE otp_codes SET verified = TRUE WHERE phone = %s", (body.phone,))
    return {"verified": True}


# ---------------------------------------------------------------- policy


@app.post("/get_policy_summary")
def get_policy_summary(body: PolicyIn) -> dict:
    p = _policy_for(body.policy_id)
    return {
        "policy_number": p["policy_number"],
        "status": p["status"],
        "effective_date": _d(p["effective_date"]),
        "renewal_date": _d(p["renewal_date"]),
        "premium_monthly": _f(p["premium_monthly"]),
        "balance_due": _f(p["balance_due"]),
        "payment_due_date": _d(p["payment_due_date"]),
        "state_code": p["state_code"],
    }


@app.post("/get_vehicles")
def get_vehicles(body: PolicyIn) -> dict:
    rows = many(
        "SELECT year, make, model, vin_last4 FROM vehicles WHERE policy_id = %s ORDER BY year DESC",
        (body.policy_id,),
    )
    return {"vehicles": rows}


@app.post("/list_coverages")
def list_coverages(body: PolicyIn) -> dict:
    rows = many(
        "SELECT c.coverage_type, k.display_name, c.is_included, c.deductible, c.limit_amount"
        " FROM coverages c JOIN coverage_catalog k ON k.coverage_type = c.coverage_type"
        " WHERE c.policy_id = %s ORDER BY k.display_name",
        (body.policy_id,),
    )
    return {
        "coverages": [
            {
                "coverage_type": r["coverage_type"],
                "display_name": r["display_name"],
                "is_included": r["is_included"],
                "deductible": _f(r["deductible"]),
                "limit_amount": _f(r["limit_amount"]),
            }
            for r in rows
        ]
    }


@app.post("/get_coverage")
def get_coverage(body: CoverageIn) -> dict:
    """Answer 'is X covered and what is my deductible' from the policy, never from memory."""
    catalog = one(
        "SELECT * FROM coverage_catalog WHERE coverage_type = %s",
        (body.coverage_type,),
    )
    if not catalog:
        return {"known_coverage_type": False, "coverage_type": body.coverage_type}
    row = one(
        "SELECT * FROM coverages WHERE policy_id = %s AND coverage_type = %s",
        (body.policy_id, body.coverage_type),
    )
    if not row or not row["is_included"]:
        return {
            "known_coverage_type": True,
            "coverage_type": body.coverage_type,
            "display_name": catalog["display_name"],
            "is_included": False,
            "description": catalog["description"],
        }
    return {
        "known_coverage_type": True,
        "coverage_type": body.coverage_type,
        "display_name": catalog["display_name"],
        "is_included": True,
        "deductible": _f(row["deductible"]),
        "limit_amount": _f(row["limit_amount"]),
        "description": catalog["description"],
    }


# ---------------------------------------------------------------- claims


@app.post("/list_claims")
def list_claims(body: PolicyIn) -> dict:
    rows = many(
        "SELECT claim_ref, loss_type, loss_date, status FROM claims"
        " WHERE policy_id = %s ORDER BY opened_at DESC LIMIT 5",
        (body.policy_id,),
    )
    return {
        "claims": [
            {
                "claim_ref": r["claim_ref"],
                "loss_type": r["loss_type"],
                "loss_date": _d(r["loss_date"]),
                "status": r["status"],
            }
            for r in rows
        ]
    }


@app.post("/get_claim_status")
def get_claim_status(body: ClaimRefIn) -> dict:
    """A settlement figure is only ever returned against an explicit claim reference."""
    c = one(
        "SELECT * FROM claims WHERE claim_ref = %s", (body.claim_ref.strip().upper(),)
    )
    if not c:
        return {"found": False, "claim_ref": body.claim_ref}
    events = many(
        "SELECT occurred_at, note FROM claim_events WHERE claim_id = %s"
        " ORDER BY occurred_at DESC LIMIT 3",
        (c["claim_id"],),
    )
    return {
        "found": True,
        "claim_ref": c["claim_ref"],
        "loss_type": c["loss_type"],
        "loss_date": _d(c["loss_date"]),
        "status": c["status"],
        "adjuster_name": c["adjuster_name"],
        "adjuster_phone": c["adjuster_phone"],
        "settlement_amount": _f(c["settlement_amount"]),
        "deductible_applied": _f(c["deductible_applied"]),
        "latest_notes": [n["note"] for n in events],
    }


@app.post("/file_claim")
def file_claim(body: FileClaimIn) -> dict:
    """First Notice of Loss. Mutates the world: creates a claim and its first event."""
    existing = one(
        "SELECT claim_ref, status FROM claims WHERE idempotency_key = %s",
        (body.idempotency_key,),
    )
    if existing:
        return {
            "claim_ref": existing["claim_ref"],
            "status": existing["status"],
            "duplicate": True,
        }
    p = _policy_for(body.policy_id)
    if p["status"] != "active":
        raise HTTPException(409, "policy_not_active")
    claim_id = f"clm_{uuid.uuid4().hex[:10]}"
    claim_ref = f"CLM-{uuid.uuid4().int % 90000 + 10000}"
    adjuster, adjuster_phone = ADJUSTERS[uuid.uuid4().int % len(ADJUSTERS)]
    cov = one(
        "SELECT deductible FROM coverages WHERE policy_id = %s AND coverage_type = %s",
        (body.policy_id, body.loss_type),
    )
    run(
        "INSERT INTO claims (claim_id, claim_ref, policy_id, loss_type, loss_date,"
        " loss_location, description, other_party, status, adjuster_name, adjuster_phone,"
        " deductible_applied, idempotency_key)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'assigned',%s,%s,%s,%s)",
        (
            claim_id,
            claim_ref,
            body.policy_id,
            body.loss_type,
            body.loss_date,
            body.loss_location,
            body.description,
            body.other_party,
            adjuster,
            adjuster_phone,
            cov["deductible"] if cov else None,
            body.idempotency_key,
        ),
    )
    run(
        "INSERT INTO claim_events (event_id, claim_id, note) VALUES (%s,%s,%s)",
        (f"evt_{uuid.uuid4().hex[:10]}", claim_id, "Claim submitted by phone"),
    )
    return {
        "claim_ref": claim_ref,
        "status": "assigned",
        "adjuster_name": adjuster,
        "adjuster_phone": adjuster_phone,
        "deductible_applied": _f(cov["deductible"]) if cov else None,
        "duplicate": False,
    }


# ---------------------------------------------------------------- money


@app.post("/get_balance")
def get_balance(body: PolicyIn) -> dict:
    p = _policy_for(body.policy_id)
    return {
        "balance_due": _f(p["balance_due"]),
        "premium_monthly": _f(p["premium_monthly"]),
        "payment_due_date": _d(p["payment_due_date"]),
        "status": p["status"],
    }


@app.post("/send_payment_link_sms")
def send_payment_link_sms(body: PayLinkIn) -> dict:
    """Card details are never taken by voice. A link is sent instead."""
    link_id = f"lnk_{uuid.uuid4().hex[:10]}"
    run(
        "INSERT INTO payment_links (id, phone, policy_id, amount, status)"
        " VALUES (%s,%s,%s,%s,'pending')",
        (link_id, body.phone, body.policy_id, body.amount),
    )
    return {"link_id": link_id, "sent": True, "status": "pending"}


@app.post("/check_payment_link_status")
def check_payment_link_status(body: LinkIdIn) -> dict:
    row = one("SELECT * FROM payment_links WHERE id = %s", (body.link_id,))
    if not row:
        return {"found": False}
    # The demo link becomes usable on the first status check, so a scenario can
    # progress without a real payment page.
    if row["status"] == "pending":
        run("UPDATE payment_links SET status = 'ready' WHERE id = %s", (body.link_id,))
        return {"found": True, "status": "ready"}
    return {"found": True, "status": row["status"]}


@app.post("/send_id_card_sms")
def send_id_card_sms(body: PolicyIn) -> dict:
    p = _policy_for(body.policy_id)
    request_id = f"idc_{uuid.uuid4().hex[:10]}"
    run(
        "INSERT INTO id_card_requests (request_id, policy_id, channel) VALUES (%s,%s,'sms')",
        (request_id, body.policy_id),
    )
    return {"sent": True, "request_id": request_id, "policy_number": p["policy_number"]}


@app.post("/request_policy_cancellation")
def request_policy_cancellation(body: CancelIn) -> dict:
    """Mutating and irreversible in the demo world. The agent must confirm first."""
    p = _policy_for(body.policy_id)
    if p["status"] in {"cancelled", "pending_cancellation"}:
        return {"policy_number": p["policy_number"], "status": p["status"], "duplicate": True}
    run(
        "UPDATE policies SET status = 'pending_cancellation' WHERE policy_id = %s",
        (body.policy_id,),
    )
    return {
        "policy_number": p["policy_number"],
        "status": "pending_cancellation",
        "effective_at": _d(p["renewal_date"]),
        "duplicate": False,
    }


@app.post("/transfer_to_human")
def transfer_to_human(body: TransferIn) -> dict:
    transfer_id = f"trf_{uuid.uuid4().hex[:10]}"
    run(
        "INSERT INTO transfers (transfer_id, phone, reason, queue) VALUES (%s,%s,%s,%s)",
        (transfer_id, body.phone, body.reason, body.queue),
    )
    return {
        "transferred": True,
        "transfer_id": transfer_id,
        "queue": body.queue,
        "at": datetime.now(UTC).isoformat(),
    }
