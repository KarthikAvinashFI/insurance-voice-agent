# Meridian Mutual — auto-insurance inbound voice agent

A local-first inbound voice agent for a fictional auto-insurance carrier. Callers phone
in to report a loss, chase a claim, ask what their cover includes, pay a bill, or cancel.

Everything is synthetic: the carrier, the policyholders, the policy numbers and the
claims are all invented. There is no real customer data anywhere in this repository.

The agent runs on LiveKit with Deepgram for speech and Gemini (direct or via Vertex) for
reasoning. Policy data lives in a local Postgres, reached only through a small tools API.

## Why the tools API exists

The agent never decides a deductible, invents a claim status, or quotes a settlement.
Every figure it speaks comes back from an HTTP tool call. That separation is the point:
it makes "the agent did not invent that number" something you can actually check, because
any amount in the transcript must trace to a response from `tools-api`.

## Layout

```
agent/
  agent.py              LiveKit entrypoint (the single agent entrypoint)
  insurance_agent.py    the 20 function tools
  state.py              verify-before-disclose and confirm-before-commit, enforced
  prompt.py             the spoken system prompt
  tools_client.py       HTTP client, with tool-call tracing
  config.py             STT and LLM provider wiring
db/
  schema.sql            the world
  seed.sql              fictional policyholders, policies, claims
tools-api/
  main.py               one endpoint per tool
tests/
  test_state.py         the guarantees the prompt only promises in words
```

## What it can do

| # | Use case | Shape |
|---|---|---|
| 1 | Verify a caller by policy number plus date of birth or postcode | gate for everything else |
| 2 | File a First Notice of Loss | multi-turn, state-carrying |
| 3 | Check a claim by reference | lookup |
| 4 | Answer "is this covered, and what is my excess" | lookup |
| 5 | List everything the policy covers | lookup |
| 6 | Report the balance and text a payment link | mutating |
| 7 | Text an insurance ID card | mutating |
| 8 | Cancel a policy after an explicit read-back | mutating, irreversible |
| 9 | Refuse a vehicle, driver or coverage change and transfer | refusal path |
| 10 | Transfer on injuries, legal representation, or a denial dispute | refusal path |

The FNOL flow is deliberately the deepest: the loss type, date, location and description
arrive over several turns, are read back as one summary, and are only filed after an
explicit yes. A correction after the read-back invalidates the token and forces a re-read.

## Rules the agent is held to

These are stated in the prompt and independently enforced in `state.py`, so a test can
assert them rather than trusting the model:

- Caller ID recognises, it does not verify. Nothing account-specific is disclosed until
  `verify_identity` returns verified.
- No settlement figure is ever quoted without an explicit claim reference.
- Card details are never taken by voice; a payment link is texted instead.
- A claim is filed only after a read-back and an explicit yes.
- A policy is cancelled only after a read-back and an explicit yes.
- Vehicle, driver and coverage changes need an underwriter and are refused, then transferred.
- Injuries or legal representation stop detail-gathering and transfer immediately.
- A lapsed or cancelled policy cannot file a claim.

## Setup

Requires Docker and Python 3.11 or newer.

```bash
cp .env.example .env.local     # then fill it in; never commit it
docker compose up -d           # postgres + tools-api
curl localhost:18091/health    # {"ok": true}
```

Run the LiveKit worker against that stack:

```bash
uv sync
uv run agent/agent.py dev
```

Or run everything in containers:

```bash
docker compose --profile full up --build
```

### Environment variables

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | media transport |
| `DEEPGRAM_API_KEY` | speech to text and text to speech |
| `GEMINI_API_KEY` | reasoning, direct Gemini |
| `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | reasoning, via Vertex instead |
| `TOOLS_API_URL` | defaults to `http://localhost:18091` |
| `DEMO_CALLER_ANI` | which seeded policyholder an unattended call appears to come from |
| `AGENT_LLM_MODEL`, `AGENT_STT_MODEL`, `AGENT_TTS_MODEL` | model choices |
| `DISABLE_AI_COUSTICS` | set to `1` to skip noise cancellation |

Supply credentials through `.env.local` or the environment. Nothing secret belongs in
this repository.

## Seeded callers

| Number | Who | Exercises |
|---|---|---|
| `+14155550201` | Dana Whitfield | open collision claim, full cover, two vehicles |
| `+14155550202` | Marcus Ortega | balance due, no glass or rental cover |
| `+14155550203` | Priya Raman | lapsed policy, must be refused |
| `+14155550204` | Elena Fischer | active, no claims, clean FNOL path |
| `+14155550205` | Tobias Lindqvist | denied claim, dispute must transfer |

Any other number is treated as unrecognised and must verify from scratch.

The SMS verification code is fixed at `246813` in the seed so unattended runs are
deterministic.

## Tests

```bash
uv run pytest
```

`tests/test_state.py` covers the transactional guarantees: that caller ID alone does not
unlock disclosure, that a claim cannot be filed without a read-back and an explicit yes,
that correcting a detail after the read-back invalidates the confirmation token, and that
a lapsed policy cannot file at all.

## A note on the database schema

Every table uses a single-column primary key. Where the natural identity is compound —
a vehicle within a policy, a coverage within a policy — that is expressed as a `UNIQUE`
constraint over a surrogate key rather than a two-column `PRIMARY KEY`.

This is deliberate and worth preserving. Tooling that reconstructs this schema elsewhere
may emit each key column as its own inline `PRIMARY KEY`, which Postgres rejects with
`multiple primary keys for table X are not allowed`. Single-column keys sidestep it.
