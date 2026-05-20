import asyncio
import os
import secrets
import time

import orjson
import websockets
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from slowapi import Limiter
from slowapi.util import get_remote_address
from twilio.request_validator import RequestValidator

from app import models
from app.agent import (
    SYSTEM_PROMPT,
    CallState,
    classify_turn,
    clear_session,
    extract_call_state,
    generate_call_summary,
    get_session_meta,
    save_session,
)
from app.database import Base, engine, get_db
from app.logger import get_logger
from app.metrics import metrics_worker, put_metric

load_dotenv()
logger = get_logger("main")
security = HTTPBasic()

BASE_URL = os.getenv("BASE_URL", "")
validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN", ""))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

Base.metadata.create_all(bind=engine)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FirstCall")

app.state.limiter = limiter

PENDING_AUDIO: dict[str, str] = {}


@app.on_event("startup")
async def start_metrics_worker():
    asyncio.create_task(metrics_worker())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/voice")
@limiter.limit("60/minute")
async def handle_call(request: Request):
    """Twilio calls this endpoint when someone dials the FirstCall number."""
    form = await request.form()
    url = str(request.url).replace("http://", "https://")
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(url, dict(form), signature):
        return Response(status_code=403)
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
    url = str(request.url).replace("http://", "https://")
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(url, dict(form), signature):
        return Response(status_code=403)
    call_sid = str(form.get("CallSid") or "")
    duration_seconds = str(form.get("CallDuration") or "0")
    session_meta = await get_session_meta(call_sid)

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
    await clear_session(call_sid)
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
                orjson.dumps(
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
                                        "eagerness": "low",
                                        "create_response": False,
                                        "interrupt_response": False,
                                    },
                                },
                                "output": {
                                    "format": {"type": "audio/pcmu"},
                                    "voice": "shimmer",
                                },
                            },
                        },
                    }
                ).decode()
            )

            async def twilio_to_openai() -> None:
                nonlocal stream_sid, call_sid, country_code
                async for message in websocket.iter_text():
                    data = orjson.loads(message)
                    if data["event"] == "start":
                        put_metric("CallsConnected", 1)
                        stream_sid = data["start"]["streamSid"]
                        call_sid = data["start"]["callSid"]
                        country_code = data["start"]["customParameters"].get("country", "US")
                        logger.info("Stream started", extra={"call_sid": call_sid})
                        await openai_ws.send(
                            orjson.dumps(
                                {
                                    "type": "response.create",
                                    "response": {
                                        "instructions": "Greet the caller. Say exactly: 'Hello, this is FirstCall. Please describe the emergency.' Nothing else."
                                    },
                                }
                            ).decode()
                        )
                    elif data["event"] == "media":
                        await openai_ws.send(
                            orjson.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": data["media"]["payload"],
                                }
                            ).decode()
                        )
                    elif data["event"] == "stop":
                        logger.info("Stream stopped", extra={"call_sid": call_sid})
                        put_metric("CallsCompleted", 1)
                        if call_sid and last_state:
                            await save_session(
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
                                await save_session(
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
                _protocol_agent_running = False

                async def run_protocol_agent(
                    t: str, h: list[str], cc: str, prev: CallState | None
                ) -> None:
                    nonlocal last_state, _protocol_agent_running
                    if _protocol_agent_running:
                        return
                    _protocol_agent_running = True
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
                        # last_state is now updated — Option A uses it on the next turn
                    except Exception as e:
                        logger.error(
                            "Protocol agent error",
                            extra={"error": str(e), "call_sid": call_sid},
                        )
                        put_metric("ProtocolAgentErrors", 1)
                    finally:
                        _protocol_agent_running = False

                async for raw in openai_ws:
                    data = orjson.loads(raw)
                    event = data.get("type")

                    if event == "response.output_audio.delta" and stream_sid:
                        await websocket.send_text(
                            orjson.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": data["delta"]},
                                }
                            ).decode()
                        )

                    elif event == "conversation.item.input_audio_transcription.completed":
                        transcript = data.get("transcript", "")
                        logger.info(
                            "Transcript received",
                            extra={"transcript": transcript, "call_sid": call_sid},
                        )

                        if transcript and call_sid:
                            conversation_history.append(transcript)
                            intent = classify_turn(transcript) if last_state else "new_info"

                            if intent == "repeat" and last_state:
                                await openai_ws.send(
                                    orjson.dumps(
                                        {
                                            "type": "response.create",
                                            "response": {
                                                "instructions": (
                                                    f"The caller asked you to repeat."
                                                    f"Repeat this instruction in simpler words: {last_state.next_instruction}"
                                                )
                                            },
                                        }
                                    ).decode()
                                )
                            else:
                                if last_state:
                                    immediate_instructions = (
                                        f"New caller message: '{transcript}'\n"
                                        f"Previous context: condition={last_state.condition}, "
                                        f"severity={last_state.severity}, step={last_state.protocol_step}\n"
                                        f"Last instruction given: {last_state.next_instruction}\n"
                                        f"Respond to what the caller just said and continue the protocol."
                                    )
                                else:
                                    immediate_instructions = (
                                        f"The caller just said: '{transcript}'. "
                                        f"Assess the emergency and respond immediately following your safety rules."
                                    )
                                await openai_ws.send(
                                    orjson.dumps(
                                        {
                                            "type": "response.create",
                                            "response": {"instructions": immediate_instructions},
                                        }
                                    ).decode()
                                )
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
                        await openai_ws.send(orjson.dumps({"type": "response.cancel"}).decode())
                        if stream_sid:
                            await websocket.send_text(
                                orjson.dumps(
                                    {
                                        "event": "clear",
                                        "streamSid": stream_sid,
                                    }
                                ).decode()
                            )

                    elif event == "response.cancelled":
                        logger.info("Response cancelled", extra={"call_sid": call_sid})
                        if stream_sid:
                            await websocket.send_text(
                                orjson.dumps(
                                    {
                                        "event": "clear",
                                        "streamSid": stream_sid,
                                    }
                                ).decode()
                            )

                    elif event == "error":
                        logger.error("OpenAI error", extra={"error": data})

            await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    except Exception as e:
        logger.error("OpenAI connection failed", extra={"error": str(e)})
        put_metric("CallsFailed", 1)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):  # noqa: B008
    valid_user = secrets.compare_digest(credentials.username, os.getenv("ADMIN_USER", "admin"))
    valid_pass = secrets.compare_digest(credentials.password, os.getenv("ADMIN_PASSWORD", ""))
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


@app.get("/admin", response_class=HTMLResponse)
async def admin(credentials: HTTPBasicCredentials = Depends(require_admin)):  # noqa: B008
    return """                                                                                                                                                                                                              
      <html>                                                
        <body style="margin:0">                                                                                                                                                                                               
          <iframe src="https://cloudwatch.amazonaws.com/dashboard.html?dashboard=firstcall-prod-dashboard&context=eyJSIjoidXMtZWFzdC0xIiwiRCI6ImN3LWRiLTk1MzAwNTgxOTMxMSIsIlUiOiJ1cy1lYXN0LTFfUmZXYzkyZHY3IiwiQyI6IjduMWJlMGJlMGhyMG5nY2c3ZmhoMmViZG5jIiwiSSI6InVzLWVhc3QtMTpiODk0YmU3MC0zNjMxLTRmOGItYjZiYS01MTdiOTkzNjA0NzAiLCJNIjoiUHVibGljIn0=" width="100%" height="100%" frameborder="0"/>                                                                                                                                
        </body>                               
      </html>                                                                                                                                                                                                                 
      """
