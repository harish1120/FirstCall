# FirstCall

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-Realtime-412991?style=for-the-badge&logo=openai&logoColor=white)
![Twilio](https://img.shields.io/badge/Twilio-F22F46?style=for-the-badge&logo=twilio&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-ElastiCache-DC382D?style=for-the-badge&logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-EC2+RDS+ALB-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**Real-time voice AI for medical emergency triage and first aid guidance.**

Pick up the phone. Describe what's happening. FirstCall triages the situation, escalates to 911 if needed, and walks you through exactly what to do — step by step, in real time, until help arrives.

No app. No account. Just a phone call.

---

## Try It Live

**Call [`+1 (782) 802-0868`](tel:+17828020868) right now.**

Describe any medical emergency in plain language. The agent will triage the situation and guide you through what to do.

> Live demo running on [firstcall.help](https://firstcall.help) — deployed on AWS, available 24/7.

---

## The Problem

The average ambulance response time is 8–12 minutes. In a cardiac arrest, brain damage begins in 4–6 minutes.

Most people know they should do something. They don't know what.

When someone is bleeding in front of you, you're not opening an app or navigating a menu. You're picking up the phone. FirstCall is built for that moment.

---

## How It Works

```
You call +1 (782) 802-0868
        ↓
Twilio opens a real-time audio stream to FirstCall
        ↓
OpenAI Realtime API handles STT, conversation, and TTS simultaneously
        ↓
You describe the emergency
        ↓
Protocol Agent (gpt-5.4-nano) extracts structured call state:
  severity · condition · protocol step · next instruction · needs 911
        ↓
Critical? → "Call 911 right now. I'll stay with you."
Urgent?   → First aid + prompt for emergency transport
Routine?  → Full step-by-step guidance
        ↓
Voice Agent (gpt-realtime-2) delivers the next instruction naturally
        ↓
You speak again → Protocol Agent updates state → Voice Agent responds
        ↓
Call ends → Summary Agent generates a structured summary
        ↓
Every call logged anonymously to PostgreSQL
```

---

## Multi-Agent Architecture

FirstCall uses three specialized agents coordinated over a single WebSocket session:

| Agent | Model | Role |
|---|---|---|
| **Protocol Agent** | gpt-5.4-nano | Runs after every caller turn. Extracts severity, condition, protocol step, and decides the next instruction. Carries state across turns so step tracking persists through short confirmations ("done", "okay"). |
| **Voice Agent** | gpt-realtime-2 | The voice of the call. Delivers the Protocol Agent's `next_instruction` naturally — tone, pacing, empathy. Makes no medical decisions itself. |
| **Summary Agent** | gpt-5.4-nano | Runs at the end of the call. Reads the full conversation and produces a structured summary for the audit log. |

**Why separate them:** The Protocol Agent decides *what* to say. The Voice Agent decides *how* to say it. Keeping them separate means the LLM never makes triage decisions — those are owned by the Protocol Agent with rule-based inputs feeding it.

---

## Safety Design

The 911 escalation logic is the most critical part of the system. It is **not an LLM decision** — it is a hardcoded rule enforced before any first aid guidance is given.

| Tier | Severity | Conditions | Response |
|------|----------|------------|----------|
| 🟢 Routine | Minor | Small cuts, 1st-degree burns, mild sprains | Full first aid guidance |
| 🟡 Urgent | Moderate | Fractures, deep lacerations, head injury | First aid + prompt for emergency transport |
| 🔴 Critical | Life-threatening | Cardiac arrest, choking, stroke, anaphylaxis, severe bleeding, seizure, poisoning, drowning | **911 escalation first**, then concurrent guidance |

**The agent never tells someone not to call 911. It never positions itself as a replacement for emergency services.**

---

## Features

- **Voice-first** — No app, no account, no navigation. Just call.
- **Real-time audio** — OpenAI Realtime API handles STT, LLM, and TTS in a single WebSocket stream. No sequential pipeline latency.
- **Barge-in support** — Server-side VAD detects when the caller starts speaking mid-response and interrupts immediately. No waiting for the agent to finish.
- **Multi-agent coordination** — Protocol Agent, Voice Agent, and Summary Agent each own a distinct responsibility and run concurrently.
- **State persistence** — Protocol Agent carries severity and step count across turns using `last_state`, so short confirmations don't reset the protocol.
- **10 first aid protocols** — Cardiac arrest (CPR), choking, severe bleeding, burns, stroke, anaphylaxis, seizure, fracture, head injury, poisoning.
- **Country detection** — Auto-detects caller's country and uses the correct emergency number (911 / 999 / 112 / 000).
- **Audit log** — Every call logged with severity, condition, duration, steps completed, and whether 911 was recommended. No PII stored.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Phone | Twilio Programmable Voice + Media Streams |
| Real-time voice | OpenAI Realtime API (gpt-realtime-2, g711 μ-law, server VAD) |
| Protocol & summary | OpenAI gpt-5.4-nano (structured output via Pydantic) |
| Session state | Redis on AWS ElastiCache |
| Backend | FastAPI + Python 3.12 |
| Deploy | AWS EC2 + ALB + ACM (SSL) |
| Database | PostgreSQL on AWS RDS |
| CI/CD | GitHub Actions → AWS SSM |

---

## Architecture

```
Caller dials Twilio number
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
                │       → session.update injects state + next_instruction to Voice Agent
                ├── input_audio_buffer.speech_started → barge-in clear
                └── response.cancelled → barge-in clear
        ↓
Call ends (stop event)
        ↓
Partial session saved to Redis immediately (race-condition safe)
        ↓
Summary Agent (gpt-5.4-nano) generates CallSummary
        ↓
Full session saved to Redis
        ↓
POST /call-status (Twilio webhook) → write CallLog to PostgreSQL → clear Redis
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [ngrok](https://ngrok.com/) for local development
- Twilio account + phone number
- OpenAI API key
- Redis (local or ElastiCache)

### Installation

```bash
git clone https://github.com/harish1120/firstcall.git
cd firstcall

uv sync

cp .env.example .env
# Fill in your API keys
```

### Running Locally

```bash
# Terminal 1 — start the server
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2 — expose to Twilio
ngrok http 8000
```

Set `BASE_URL` in `.env` to your ngrok URL. Point your Twilio number's voice webhook to `https://your-ngrok-url/voice` and status callback to `https://your-ngrok-url/call-status`, then call it.

### Tests

```bash
uv run pytest
```

### Lint & Format

```bash
uv run ruff check .
uv run ruff format .
```

---

## Environment Variables

```env
APP_ENV=development
BASE_URL=https://your-ngrok-url

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

OPENAI_API_KEY=

REDIS_HOST=localhost
REDIS_SSL=false

RDS_HOST=
RDS_USER=postgres
RDS_DB=postgres
```

---

## First Aid Protocols

FirstCall covers 10 emergency scenarios, written in plain language designed to be spoken aloud and followed under panic:

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

---

## Liability

FirstCall is designed to complement emergency services, not replace them. Every critical response begins with an instruction to call 911. All first aid protocols are based on established guidelines (Red Cross, AHA). The system logs every call for audit purposes with no personally identifiable information stored.

---

## License

MIT — see [LICENSE](LICENSE) for details.
