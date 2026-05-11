import asyncio
import json
import os

import websockets
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, WebSocket, status
from fastapi.responses import Response, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from twilio.request_validator import RequestValidator

from app import models
from app.agent import SYSTEM_PROMPT, clear_session, get_session_meta, save_session
from app.database import Base, engine, get_db
from app.protocols.loader import get_first_aid_protocol
from app.triage import get_emergency_number, triage_severity
from app.tts import intro_speech, text_to_speech_stream

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "")
validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN", ""))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

Base.metadata.create_all(bind=engine)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FirstCall")

app.state.limiter = limiter

PENDING_AUDIO: dict[str, str] = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/voice")
@limiter.limit("60/minute")
async def handle_call(request: Request):
    """Twilio calls this endpoint when someone dials the FirstCall number."""
    form = await request.form()
    country = form.get("FromCountry", "US")
    ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>{BASE_URL}/play-intro</Play>
        <Connect>
            <Stream url="{ws_url}/stream">
                <Parameter name="country" value="{country}"/>
            </Stream>
        </Connect>
    </Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.get("/play-intro")
async def play_intro():
    text = "Hello, this is FirstCall. Please describe the emergency!"
    if not text:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return StreamingResponse(intro_speech(text), media_type="audio/mpeg")


@app.get("/audio/{call_sid}")
async def audio_stream(call_sid: str):
    text = PENDING_AUDIO.pop(call_sid, None)
    if not text:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return StreamingResponse(text_to_speech_stream(text), media_type="audio/mpeg")


@app.post("/handle-recording")
async def handle_recording(request: Request):
    """Receives the recorded audio URL from Twilio after the caller speaks."""
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>Thank you. Processing your request.</Say>
    </Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.api_route("/call-status", methods=["GET", "POST"], status_code=status.HTTP_201_CREATED)
async def call_status(request: Request, db=Depends(get_db)):  # noqa: B008
    form = await request.form()
    call_sid = str(form.get("CallSid") or "")
    duration_seconds = str(form.get("CallDuration") or "0")
    session_meta = get_session_meta(call_sid)

    if not session_meta:
        return {"status": "no session"}

    call_log = models.CallLog(
        call_sid=call_sid,
        duration_seconds=int(duration_seconds),
        severity=session_meta["severity"],
        condition=session_meta["condition"],
    )
    db.add(call_log)
    db.commit()
    clear_session(call_sid)
    return {"status": "logged"}


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()

    stream_sid: str | None = None
    call_sid: str | None = None
    country_code: str = "US"
    triage_done: bool = False

    print(f"[OpenAI] Connecting... API key set: {bool(OPENAI_API_KEY)}")
    try:
        async with websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-realtime",
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as openai_ws:
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "voice": "shimmer",
                            "temperature": 0.3,
                            "input_audio_format": "g711_ulaw",
                            "output_audio_format": "g711_ulaw",
                            "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.4,
                                "silence_duration_ms": 400,
                                "prefix_padding_ms": 300,
                            },
                            "instructions": SYSTEM_PROMPT,
                        },
                    }
                )
            )

            async def twilio_to_openai() -> None:
                nonlocal stream_sid, call_sid, country_code
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data["event"] == "start":
                        stream_sid = data["start"]["streamSid"]
                        call_sid = data["start"]["callSid"]
                        country_code = data["start"]["customParameters"].get("country", "US")
                        print(f"[WS] Stream started: call_sid={call_sid}")
                        await openai_ws.send(
                            json.dumps(
                                {
                                    "type": "response.create",
                                    "response": {
                                        "instructions": "Greet the caller. Say exactly: 'Hello, this is FirstCall. Please describe the emergency.' Nothing else."
                                    },
                                }
                            )
                        )
                    elif data["event"] == "media":
                        await openai_ws.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": data["media"]["payload"],
                                }
                            )
                        )
                    elif data["event"] == "stop":
                        print("[WS] Stream stopped")
                        break

            async def openai_to_twilio() -> None:
                nonlocal triage_done
                async for raw in openai_ws:
                    data = json.loads(raw)
                    event = data.get("type")

                    if event == "response.audio.delta" and stream_sid:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": data["delta"]},
                                }
                            )
                        )

                    elif event == "conversation.item.input_audio_transcription.completed":
                        transcript = data.get("transcript", "")
                        print(f"[TRANSCRIPT] {transcript}")

                        if not triage_done and transcript and call_sid:
                            triage_done = True
                            severity = triage_severity(transcript)
                            emergency_number = get_emergency_number(country_code)
                            protocol = get_first_aid_protocol(transcript)
                            print(f"[TRIAGE] severity={severity}")

                            critical_override = (
                                (
                                    "\n\nCRITICAL OVERRIDE — THIS IS A LIFE-THREATENING EMERGENCY:\n"
                                    f"- Severity is CRITICAL. Emergency number is {emergency_number}.\n"
                                    f"- Your NEXT response must start with 'Call {emergency_number} right now.' No exceptions.\n"
                                    f"- If the caller asks whether to call {emergency_number}, say YES immediately.\n"
                                    f"- Protocol to follow after 911 is called: {protocol}\n"
                                )
                                if str(severity) == "CRITICAL"
                                else (
                                    f"\n\nCurrent situation:\n"
                                    f"- Severity: {severity}\n"
                                    f"- Emergency number: {emergency_number}\n"
                                    f"- Protocol: {protocol}\n"
                                )
                            )
                            updated = SYSTEM_PROMPT + critical_override
                            await openai_ws.send(
                                json.dumps(
                                    {
                                        "type": "session.update",
                                        "session": {"instructions": updated},
                                    }
                                )
                            )
                            save_session(
                                call_sid,
                                {
                                    "severity": severity,
                                    "condition": transcript,
                                    "messages": [],
                                },
                            )

                    elif event == "input_audio_buffer.speech_started":
                        print("[BARGE-IN] Speech detected, clearing Twilio buffer")
                        if stream_sid:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "clear",
                                        "streamSid": stream_sid,
                                    }
                                )
                            )

                    elif event == "response.cancelled":
                        print("[BARGE-IN] Response cancelled")
                        if stream_sid:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "clear",
                                        "streamSid": stream_sid,
                                    }
                                )
                            )

                    elif event == "error":
                        print(f"[OpenAI Error] {data}")

            await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    except Exception as e:
        print(f"[OpenAI] Connection failed: {e}")
