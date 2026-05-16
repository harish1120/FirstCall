import asyncio
import json
import os
import time

import websockets
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, WebSocket, status
from fastapi.responses import Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from twilio.request_validator import RequestValidator

from app import models
from app.agent import (
    SYSTEM_PROMPT,
    CallState,
    clear_session,
    extract_call_state,
    generate_call_summary,
    get_session_meta,
    save_session,
)
from app.database import Base, engine, get_db
from app.logger import get_logger
from app.metrics import put_metric

load_dotenv()
logger = get_logger("main")

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
    put_metric("CallsIncoming", 1)
    country = form.get("FromCountry", "US")
    ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="{ws_url}/stream">
                <Parameter name="country" value="{country}"/>
            </Stream>
        </Connect>
    </Response>"""
    return Response(content=twiml, media_type="application/xml")


# @app.get("/audio/{call_sid}")
# async def audio_stream(call_sid: str):
#     text = PENDING_AUDIO.pop(call_sid, None)
#     if not text:
#         return Response(status_code=status.HTTP_404_NOT_FOUND)
#     return StreamingResponse(text_to_speech_stream(text), media_type="audio/mpeg")


# @app.post("/handle-recording")
# async def handle_recording(request: Request):
#     """Receives the recorded audio URL from Twilio after the caller speaks."""
#     twiml = """<?xml version="1.0" encoding="UTF-8"?>
#     <Response>
#         <Say>Thank you. Processing your request.</Say>
#     </Response>"""
#     return Response(content=twiml, media_type="application/xml")


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
        summary=session_meta.get("summary"),
        steps_completed=session_meta.get("steps_completed"),
        called_911=session_meta.get("called_911"),
    )
    db.add(call_log)
    db.commit()
    put_metric("CallDuration", int(duration_seconds), unit="Seconds")
    put_metric("CallsBySeverity", 1, dimensions={"Severity": session_meta["severity"]})
    if session_meta.get("called_911"):
        put_metric("Called911", 1)
    clear_session(call_sid)
    return {"status": "logged"}


@app.websocket("/stream")
async def stream(
    websocket: WebSocket,
):
    await websocket.accept()

    conversation_history: list[str] = []
    stream_sid: str | None = None
    call_sid: str | None = None
    country_code: str = "US"
    last_state: CallState | None = None

    logger.info("OpenAI connecting", extra={"api_key_set": bool(OPENAI_API_KEY)})
    try:
        async with websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-realtime-2",
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        ) as openai_ws:
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "instructions": SYSTEM_PROMPT,
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcmu"},
                                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                                    "turn_detection": {
                                        "type": "semantic_vad",
                                        "threshold": 0.8,
                                        "silence_duration_ms": 400,
                                        "prefix_padding_ms": 300,
                                    },
                                },
                                "output": {
                                    "format": {"type": "audio/pcmu"},
                                    "voice": "shimmer",
                                },
                            },
                        },
                    }
                )
            )

            async def twilio_to_openai() -> None:
                nonlocal stream_sid, call_sid, country_code
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data["event"] == "start":
                        put_metric("CallsConnected", 1)
                        stream_sid = data["start"]["streamSid"]
                        call_sid = data["start"]["callSid"]
                        country_code = data["start"]["customParameters"].get("country", "US")
                        logger.info("Stream started", extra={"call_sid": call_sid})
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
                        logger.info("Stream stopped", extra={"call_sid": call_sid})
                        put_metric("CallsCompleted", 1)
                        if call_sid and last_state:
                            save_session(
                                call_sid,
                                {
                                    "severity": last_state.severity,
                                    "condition": last_state.condition,
                                    "summary": None,
                                    "steps_completed": last_state.protocol_step,
                                    "key_actions_taken": [],
                                    "called_911": last_state.needs_911,
                                },
                            )
                        if conversation_history and call_sid:
                            try:
                                summary = await generate_call_summary(
                                    conversation_history, country_code
                                )
                                logger.info(
                                    "Summary generated",
                                    extra={"summary": summary.summary, "call_sid": call_sid},
                                )
                                save_session(
                                    call_sid,
                                    {
                                        "severity": summary.severity,
                                        "condition": summary.condition,
                                        "summary": summary.summary,
                                        "steps_completed": summary.steps_completed,
                                        "key_actions_taken": summary.key_actions_taken,
                                        "called_911": summary.called_911,
                                    },
                                )
                            except Exception as e:
                                logger.error(
                                    "Summary error", extra={"error": str(e), "call_sid": call_sid}
                                )
                                put_metric("SummaryAgentErrors", 1)
                        break

            async def openai_to_twilio() -> None:
                nonlocal last_state

                async def run_protocol_agent(
                    t: str, h: list[str], cc: str, prev: CallState | None
                ) -> None:
                    nonlocal last_state
                    try:
                        t0 = time.monotonic()
                        state = await extract_call_state(t, h, cc, prev)
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        put_metric("TranscriptToResponseMs", elapsed_ms, unit="Milliseconds")
                        last_state = state
                        logger.info(
                            "Protocol agent state",
                            extra={
                                "step": state.protocol_step,
                                "severity": state.severity,
                                "confirmed": state.caller_confirmed,
                                "call_sid": call_sid,
                                "latency_ms": round(elapsed_ms),
                            },
                        )
                        call_state_block = (
                            f"\n\n--- CURRENT CALL STATE (from Protocol Agent) ---"
                            f"\nSeverity: {state.severity}"
                            f"\nCondition: {state.condition}"
                            f"\nProtocol step: {state.protocol_step}"
                            f"\nCaller confirmed last action: {state.caller_confirmed}"
                            f"\nNeeds 911: {state.needs_911}"
                            f"\nProtocol complete: {state.protocol_complete}"
                            f"\n\nSAY THIS NEXT: {state.next_instruction}"
                            f"\n--- END CALL STATE ---"
                        )
                        await openai_ws.send(
                            json.dumps(
                                {
                                    "type": "session.update",
                                    "session": {
                                        "instructions": SYSTEM_PROMPT + call_state_block,
                                    },
                                }
                            )
                        )
                    except Exception as e:
                        logger.error(
                            "Protocol agent error",
                            extra={"error": str(e), "call_sid": call_sid},
                        )
                        put_metric("ProtocolAgentErrors", 1)

                async for raw in openai_ws:
                    data = json.loads(raw)
                    event = data.get("type")

                    if event == "response.output_audio.delta" and stream_sid:
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
                        logger.info(
                            "Transcript received",
                            extra={"transcript": transcript, "call_sid": call_sid},
                        )

                        if transcript and call_sid:
                            conversation_history.append(transcript)
                            asyncio.create_task(
                                run_protocol_agent(
                                    transcript,
                                    list(conversation_history),
                                    country_code,
                                    last_state,
                                )
                            )

                    elif event == "input_audio_buffer.speech_started":
                        logger.info("Barge-in detected", extra={"call_sid": call_sid})
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
                        logger.info("Response cancelled", extra={"call_sid": call_sid})
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
                        logger.error("OpenAI error", extra={"error": data})

            await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    except Exception as e:
        logger.error("OpenAI connection failed", extra={"error": str(e)})
        put_metric("CallsFailed", 1)
