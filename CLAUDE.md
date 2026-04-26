# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FirstCall** is a voice-based AI agent for medical emergency triage and first aid guidance. Users call a phone number, describe the emergency in plain language, and the AI triages severity, escalates to 911 when needed, and guides them through first aid step-by-step — voice-first, no app required.

**Status:** Planning phase. The `project-firstcall-agent.md` blueprint is the authoritative design document.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Phone | Twilio Programmable Voice (webhook) |
| STT | Deepgram Streaming API |
| Agent | Claude API (tool use + streaming) |
| TTS | ElevenLabs (Phase 1: Twilio built-in) |
| Backend | FastAPI + Python |
| Deploy | AWS EC2 + Docker |
| Audit log | SQLite → PostgreSQL |
| Config | YAML (triage thresholds, emergency numbers by country) |

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

# Docker build and run
docker build -t firstcall .
docker run -p 8000:8000 --env-file .env firstcall
```

Environment variables needed in `.env`:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `DEEPGRAM_API_KEY`
- `ANTHROPIC_API_KEY`
- `ELEVENLABS_API_KEY`

---

## Architecture

```
Twilio webhook (incoming call)
    ↓
FastAPI backend (AWS EC2)
    ↓
Deepgram streaming STT  (<300ms latency target)
    ↓
Claude agent — triage + guidance loop
    ├── triage_severity(description) → ROUTINE | URGENT | CRITICAL
    ├── get_emergency_number(country) → 911 / 999 / 112
    ├── get_first_aid_protocol(condition) → step-by-step instructions
    └── adapt_instructions(feedback) → simplify / repeat / next step
    ↓
Severity gate (HITL escalation logic)
    ↓
ElevenLabs TTS → Twilio → caller hears voice  (<500ms to first word)
    ↓
Stateful multi-turn conversation
    ↓
Audit log: timestamp | triage tier | condition | duration (no PII)
```

**Latency target:** End-to-end speech-in to speech-out < 1.5 seconds. Requires streaming STT + streaming LLM + streaming TTS throughout.

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

## First Aid Protocols (MVP Top 10)

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

Each protocol must be written in plain language designed to be spoken aloud and followed under panic. Phase 1 covers the top 5 (cardiac arrest, choking, bleeding, stroke, burns).

---

## Build Phases

**Phase 1 (Week 1 — Voice Loop MVP):** Twilio webhook → FastAPI → Deepgram STT → Claude triage → Twilio TTS. Top 5 protocols. Tier 3 escalation fires correctly. Audit log in place.
- Milestone: Call the number, describe a cardiac arrest, hear correct response in < 2 seconds.

**Phase 2 (Week 2 — Full Agent):** All 10 protocols, multi-turn conversation, `adapt_instructions()` tool, ElevenLabs TTS, all 3 tiers, country detection for emergency numbers.

**Phase 3 (Week 3+ — Polish):** Landing page, web demo, multilingual support (Deepgram 30+ languages), post-call SMS summary, metrics dashboard.
