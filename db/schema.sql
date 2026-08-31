-- Meridian Mutual auto-insurance voice agent — world schema.
-- Every coverage limit, deductible, claim status and payment the agent speaks
-- must come from here via a tool call. Nothing is computed in the prompt.
--
-- PRIMARY KEY POLICY: every table uses a SINGLE-COLUMN surrogate key.
-- Natural compound identities are expressed with UNIQUE constraints instead.
-- A two-column PRIMARY KEY breaks the harness world seeder, which emits each
-- key column as its own inline PRIMARY KEY and is then rejected by Postgres
-- with "multiple primary keys for table X are not allowed".

DROP TABLE IF EXISTS transfers, id_card_requests, payment_links, payments,
    claim_events, claims, coverages, drivers, vehicles, policies,
    policyholders, otp_codes, coverage_catalog CASCADE;

CREATE TABLE policyholders (
    policyholder_id TEXT PRIMARY KEY,
    phone           TEXT UNIQUE NOT NULL,        -- E.164, matched against caller_ani
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    date_of_birth   DATE NOT NULL,               -- identity factor, never spoken back
    zip_code        TEXT NOT NULL,               -- alternate identity factor
    email           TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE policies (
    policy_id       TEXT PRIMARY KEY,
    policyholder_id TEXT NOT NULL REFERENCES policyholders(policyholder_id) ON DELETE CASCADE,
    policy_number   TEXT UNIQUE NOT NULL,        -- spoken identifier, e.g. MM-4471902
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','lapsed','cancelled','pending_cancellation')),
    effective_date  DATE NOT NULL,
    renewal_date    DATE NOT NULL,
    premium_monthly NUMERIC(10,2) NOT NULL,
    balance_due     NUMERIC(10,2) NOT NULL DEFAULT 0,
    payment_due_date DATE,
    state_code      TEXT NOT NULL DEFAULT 'CA'
);

CREATE TABLE vehicles (
    vehicle_id  TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    year        INT  NOT NULL,
    make        TEXT NOT NULL,
    model       TEXT NOT NULL,
    vin_last4   TEXT NOT NULL,                   -- only the last four are ever spoken
    UNIQUE (policy_id, vin_last4)
);

CREATE TABLE drivers (
    driver_id   TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    full_name   TEXT NOT NULL,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (policy_id, full_name)
);

-- Reference list of what a coverage type means, so the agent can explain in words.
CREATE TABLE coverage_catalog (
    coverage_type TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    description   TEXT NOT NULL
);

CREATE TABLE coverages (
    coverage_id    TEXT PRIMARY KEY,
    policy_id      TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    coverage_type  TEXT NOT NULL REFERENCES coverage_catalog(coverage_type),
    is_included    BOOLEAN NOT NULL DEFAULT TRUE,
    deductible     NUMERIC(10,2),
    limit_amount   NUMERIC(12,2),
    UNIQUE (policy_id, coverage_type)            -- NOT a composite primary key
);

CREATE TABLE claims (
    claim_id        TEXT PRIMARY KEY,
    claim_ref       TEXT UNIQUE NOT NULL,        -- spoken identifier, e.g. CLM-88213
    policy_id       TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    loss_type       TEXT NOT NULL
                    CHECK (loss_type IN ('collision','comprehensive','glass','theft','weather','other')),
    loss_date       DATE NOT NULL,
    loss_location   TEXT,
    description     TEXT,
    other_party     TEXT,
    status          TEXT NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted','assigned','inspection_scheduled','in_review','approved','paid','denied','closed')),
    adjuster_name   TEXT,
    adjuster_phone  TEXT,
    settlement_amount NUMERIC(12,2),             -- only spoken when a claim_ref is supplied
    deductible_applied NUMERIC(10,2),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key TEXT UNIQUE
);

CREATE TABLE claim_events (
    event_id    TEXT PRIMARY KEY,
    claim_id    TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT NOT NULL
);

CREATE TABLE payments (
    payment_id  TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    amount      NUMERIC(10,2) NOT NULL,
    method      TEXT NOT NULL,                   -- pay_link|autopay|agent
    status      TEXT NOT NULL DEFAULT 'posted',
    paid_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key TEXT UNIQUE
);

CREATE TABLE payment_links (
    id         TEXT PRIMARY KEY,
    phone      TEXT NOT NULL,
    policy_id  TEXT REFERENCES policies(policy_id) ON DELETE CASCADE,
    amount     NUMERIC(10,2),
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','ready','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE id_card_requests (
    request_id  TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL REFERENCES policies(policy_id) ON DELETE CASCADE,
    channel     TEXT NOT NULL DEFAULT 'sms',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Step-up verification. Code is fixed in seed for deterministic scenarios.
CREATE TABLE otp_codes (
    phone         TEXT PRIMARY KEY,
    code          TEXT NOT NULL,
    attempts_left INT  NOT NULL DEFAULT 3,
    verified      BOOLEAN NOT NULL DEFAULT FALSE,
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE transfers (
    transfer_id TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    queue       TEXT NOT NULL DEFAULT 'general',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON policies (policyholder_id);
CREATE INDEX ON vehicles (policy_id);
CREATE INDEX ON coverages (policy_id);
CREATE INDEX ON claims (policy_id, opened_at DESC);
CREATE INDEX ON claim_events (claim_id, occurred_at DESC);
CREATE INDEX ON payments (policy_id, paid_at DESC);
