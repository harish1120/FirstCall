# FirstCall

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white)
![Twilio](https://img.shields.io/badge/Twilio-F22F46?style=for-the-badge&logo=twilio&logoColor=white)
![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS-black?style=for-the-badge&logoColor=white)
![Deepgram](https://img.shields.io/badge/Deepgram-STT-101010?style=for-the-badge&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-EC2-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**AI-powered voice agent for medical emergency triage and first aid guidance.**

Pick up the phone. Describe what's happening. FirstCall triages the situation, escalates to 911 if needed, and walks you through exactly what to do — step by step, in real time, until help arrives.

No app. No account. Just a phone call.

---

## The Problem

The average ambulance response time is 8–12 minutes. In a cardiac arrest, brain damage begins in 4–6 minutes.

Most people know they should do something. They don't know what.

When someone is bleeding in front of you, you're not opening an app or navigating a menu. You're picking up the phone. FirstCall is built for that moment.

---

## How It Works

```
You call the number
        ↓
Describe the emergency in plain language
        ↓
FirstCall triages severity in seconds
        ↓
Critical? → "Call 911 right now. While you wait, here's what to do."
Urgent?   → Immediate first aid + prompt for emergency transport
Routine?  → Full step-by-step guidance
        ↓
Guided through each step. One at a time. At your pace.
        ↓
Ask follow-up questions. The agent stays on the line.
```

---

## Features

- **Voice-first** — No app, no account, no navigation. Just call.
- **Instant triage** — Classifies severity (ROUTINE / URGENT / CRITICAL) in the first response.
- **HITL escalation** — Life-threatening emergencies trigger 911 escalation before any first aid guidance. This is a hardcoded rule, not an LLM decision.
- **Step-by-step guidance** — One instruction at a time, waits for confirmation before moving on.
- **Stateful conversation** — Remembers what's been said. Adapts when the caller says "I don't understand" or "what next."
- **10 first aid protocols** — Cardiac arrest, choking, severe bleeding, burns, stroke, anaphylaxis, seizure, fracture, head injury, poisoning.
- **Natural voice** — ElevenLabs TTS for calm, clear audio that doesn't sound robotic in a crisis.
- **Audit log** — Every call logged with triage tier, condition, and duration. No PII stored.

---

## Escalation Tiers

| Tier | Severity | Response |
|------|----------|----------|
| 🟢 Routine | Minor injuries — small cuts, 1st-degree burns, mild sprains | Full first aid guidance |
| 🟡 Urgent | Needs care within hours — fractures, deep lacerations, head injury | First aid + prompt for emergency transport |
| 🔴 Critical | Life-threatening — cardiac arrest, choking, stroke, anaphylaxis | **911 escalation first**, then concurrent guidance |

**The agent never tells someone not to call 911. It is the bridge between the emergency and the ambulance arriving.**

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Phone | Twilio Programmable Voice |
| STT | Deepgram Streaming API |
| Agent | OpenAI GPT-4o |
| TTS | ElevenLabs (eleven_turbo_v2) |
| Backend | FastAPI + Python 3.12 |
| Deploy | AWS EC2 + Docker |
| Database | SQLite → PostgreSQL |

---

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [ngrok](https://ngrok.com/) (for local development)
- Twilio account + phone number
- OpenAI API key
- ElevenLabs API key

### Installation

```bash
git clone https://github.com/yourusername/firstcall.git
cd firstcall

uv sync

cp .env.example .env
# Fill in your API keys
```

### Running Locally

```bash
# Terminal 1 — start the server
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2 — expose it to Twilio
ngrok http 8000
```

Point your Twilio number's webhook to `https://your-ngrok-url/voice` and call it.

### Running Tests

```bash
uv run pytest
```

### Linting

```bash
uv run ruff check .
uv run ruff format .
```

---

## Environment Variables

```env
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

OPENAI_API_KEY=

ELEVENLABS_API_KEY=

DEEPGRAM_API_KEY=
```

---

## First Aid Protocols

FirstCall covers the 10 most critical emergency scenarios, written in plain language designed to be spoken aloud and followed under panic:

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

FirstCall is designed to complement emergency services, not replace them. Every critical response begins with an instruction to call 911. All first aid protocols are based on established guidelines (Red Cross, AHA). The system logs every call for audit purposes.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
