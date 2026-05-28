# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FirstCall** is a voice-based AI agent for medical emergency triage and first aid guidance. Users call a phone number, describe the emergency in plain language, and the AI triages severity, escalates to 911 when needed, and guides them through first aid step-by-step — voice-first, no app required.

**Status:** Live at [firstcall.help](https://firstcall.help) — deployed on AWS Lightsail (us-east-1).

---

## Stack

| Layer | Technology |
|-------|-----------|
| Phone | Twilio Programmable Voice + Media Streams |
| Real-time voice | OpenAI Realtime API (gpt-realtime-2, g711 μ-law, semantic VAD) |
| Protocol & summary | OpenAI gpt-5.4-nano (structured output via Pydantic) |
| Session state | Redis (localhost, same server) |
| Backend | FastAPI + Python 3.12 |
| Deploy | AWS Lightsail (us-east-1) + nginx + systemd + Let's Encrypt |
| Database | SQLite (local file) |
| Observability | CloudWatch custom metrics + admin dashboard |

---

## Commands

```bash
# Install dependencies (creates .venv automatically)
uv sync

# Run the FastAPI server locally
uv run uvicorn app.main:app --reload --port 8000

# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy .
```

Environment variables needed in `.env` (see `.env.example`):
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `OPENAI_API_KEY`
- `REDIS_HOST`, `REDIS_SSL`
- `ADMIN_USER`, `ADMIN_PASSWORD`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` (production only)

---

## Architecture

```
Twilio webhook (incoming call)
    ↓
POST /voice → FastAPI returns TwiML (opens WebSocket stream)
    ↓
WebSocket /stream ←→ Twilio Media Streams
    |
    ├── twilio_to_openai(): forwards g711 audio chunks to OpenAI Realtime
    └── openai_to_twilio(): forwards audio back, handles events
            |
            ├── response.output_audio.delta → audio to caller
            ├── conversation.item.input_audio_transcription.completed
            │       → Protocol Agent (gpt-5.4-nano) extracts CallState
            │       → session.update injects next_instruction to Voice Agent
            ├── input_audio_buffer.speech_started → barge-in clear
            └── response.cancelled → barge-in clear
    ↓
Call ends (stop event)
    ↓
Summary Agent (gpt-5.4-nano) generates CallSummary
    ↓
Session saved to Redis (with avg latency + TTFT)
    ↓
POST /call-status (Twilio webhook) → write CallLog to SQLite → clear Redis
```

**Latency target:** End-to-end speech-in to speech-out < 1.5 seconds via OpenAI Realtime (single WebSocket, no STT→LLM→TTS pipeline).

---

## Critical Design Constraint: HITL Escalation

The three-tier escalation logic is the most safety-critical part of the system. It is **not decided by the LLM** — it is a rule-based gate.

### Tier 1 — Routine
Minor injuries (small cuts, 1st-degree burns, insect bites, mild sprains). Agent guides fully, recommends doctor if not improving.

### Tier 2 — Urgent
Needs medical attention within hours (deep lacerations, suspected fractures, moderate burns, conscious head injury). Agent gives first aid and prompts for emergency transport.

### Tier 3 — Critical (escalate first, guide concurrent)
Life-threatening conditions trigger 911 escalation **before** any first aid:

| Condition | Trigger |
|-----------|---------|
| Cardiac arrest | Unresponsive, not breathing normally |
| Choking | Cannot speak, turning blue |
| Severe bleeding | Not slowing, soaking through |
| Stroke | Face drooping, arm weakness, speech slurred |
| Anaphylaxis | Throat closing, known allergen |
| Seizure | Convulsing, not stopping |
| Poisoning | Ingested substance, altered consciousness |
| Drowning | Pulled from water, unresponsive |

**Hardcoded Tier 3 response pattern:**
> "This is a 911 emergency. Call 911 right now — I'll stay with you. Tell the operator [key info]. While you wait, here's what to do: [steps]."

**The agent never tells someone NOT to call 911. It never positions itself as a replacement for emergency services.**

---

## First Aid Protocols (10 covered)

1. Cardiac arrest (CPR)
2. Choking (adult + child)
3. Severe bleeding
4. Burns (1st, 2nd, 3rd degree)
5. Suspected stroke (FAST assessment)
6. Anaphylaxis / allergic reaction
7. Seizure
8. Fracture / suspected broken bone
9. Head injury
10. Poisoning / overdose
