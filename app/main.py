import asyncio
import base64
import json as stdlib_json
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
from app.triage import Severity, get_emergency_number, triage_severity

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
        avg_latency_ms=session_meta.get("avg_latency_ms"),
        avg_ttft_ms=session_meta.get("avg_ttft_ms"),
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
    latency_samples: list[float] = []
    ttft_samples: list[float] = []
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
                                        "type": "server_vad",
                                        "threshold": 0.8,  # 0.0-1.0, higher = less sensitive
                                        "prefix_padding_ms": 300,  # speech must be present for 300ms before triggering
                                        "silence_duration_ms": 500,  # 500ms of silence = end of turn
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
                                    "avg_latency_ms": (
                                        sum(latency_samples) / len(latency_samples)
                                        if latency_samples
                                        else None
                                    ),
                                    "avg_ttft_ms": (
                                        sum(ttft_samples) / len(ttft_samples)
                                        if ttft_samples
                                        else None
                                    ),
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
                                        "avg_latency_ms": (
                                            sum(latency_samples) / len(latency_samples)
                                            if latency_samples
                                            else None
                                        ),
                                        "avg_ttft_ms": (
                                            sum(ttft_samples) / len(ttft_samples)
                                            if ttft_samples
                                            else None
                                        ),
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
                _response_start: float | None = None

                async def send_response(instructions: str) -> None:
                    nonlocal _response_start
                    _response_start = time.monotonic()
                    await openai_ws.send(
                        orjson.dumps(
                            {"type": "response.create", "response": {"instructions": instructions}}
                        ).decode()
                    )

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
                        latency_samples.append(elapsed_ms)
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
                        if _response_start is not None:
                            ttft_ms = (time.monotonic() - _response_start) * 1000
                            put_metric("TimeToFirstTokenMs", ttft_ms, unit="Milliseconds")
                            ttft_samples.append(ttft_ms)
                            logger.info(
                                "Time to first token",
                                extra={"ttft_ms": round(ttft_ms), "call_sid": call_sid},
                            )
                            _response_start = None
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
                                await send_response(
                                    f"The caller asked you to repeat. "
                                    f"Repeat this instruction in simpler words: {last_state.next_instruction}"
                                )
                            else:
                                live_severity = triage_severity(transcript)
                                emergency_number = get_emergency_number(country_code)
                                if live_severity == Severity.CRITICAL:
                                    immediate_instructions = (
                                        f"CRITICAL EMERGENCY. Caller said: '{transcript}'. "
                                        f"Your first words must be: "
                                        f"'Call {emergency_number} right now, I'll stay with you.' "
                                        f"Then begin first aid guidance."
                                    )
                                elif last_state:
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
                                await send_response(immediate_instructions)
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
                        code = data.get("error", {}).get("code", "")
                        if code == "response_cancel_not_active":
                            pass
                        else:
                            logger.error("OpenAI error", extra={"error": data})

            await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    except Exception as e:
        logger.error("OpenAI connection failed", extra={"error": str(e)})
        put_metric("CallsFailed", 1)


def _cloudwatch_graph_b64() -> str:
    """Fetch latency+TTFT graph from CloudWatch as base64 PNG. Returns empty string on failure."""
    try:
        import boto3

        cw = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"))
        widget = {
            "width": 800,
            "height": 180,
            "start": "-PT24H",
            "end": "PT0H",
            "theme": "dark",
            "title": "Latency & TTFT — last 24h",
            "view": "timeSeries",
            "stat": "Average",
            "period": 300,
            "metrics": [
                [
                    "FirstCall",
                    "TranscriptToResponseMs",
                    {"label": "Latency (ms)", "color": "#60efb0"},
                ],
                [
                    "FirstCall",
                    "TimeToFirstTokenMs",
                    {"label": "TTFT (ms)", "color": "#7dd3fc"},
                ],
            ],
        }
        resp = cw.get_metric_widget_image(MetricWidget=stdlib_json.dumps(widget))
        return base64.b64encode(resp["MetricWidgetImage"]).decode()
    except Exception as e:
        logger.warning("CloudWatch graph fetch failed", extra={"error": str(e)})
        return ""


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):  # noqa: B008
    valid_user = secrets.compare_digest(credentials.username, os.getenv("ADMIN_USER", "admin"))
    valid_pass = secrets.compare_digest(credentials.password, os.getenv("ADMIN_PASSWORD", ""))
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return credentials


@app.get("/admin/calls")
async def admin_calls(
    credentials: HTTPBasicCredentials = Depends(require_admin),  # noqa: B008
    db=Depends(get_db),  # noqa: B008
):
    rows = (
        db.query(models.CallLog)
        .order_by(models.CallLog.created_at.desc(), models.CallLog.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "call_sid": r.call_sid,
            "severity": r.severity.value,
            "condition": r.condition,
            "duration_seconds": r.duration_seconds,
            "summary": r.summary,
            "steps_completed": r.steps_completed,
            "called_911": r.called_911,
            "avg_latency_ms": r.avg_latency_ms,
            "avg_ttft_ms": r.avg_ttft_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@app.get("/admin", response_class=HTMLResponse)
async def admin(credentials: HTTPBasicCredentials = Depends(require_admin)):  # noqa: B008
    graph_b64 = await asyncio.to_thread(_cloudwatch_graph_b64)
    graph_html = (
        f'<img src="data:image/png;base64,{graph_b64}" style="width:100%;border-radius:6px;display:block"/>'
        if graph_b64
        else '<p style="color:#7d8a82;text-align:center;padding:40px 0">No CloudWatch data yet — make a call first.</p>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>FirstCall — Admin</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f1614; color: #e8efe9; font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; font-size: 14px; }}
  .header {{ padding: 16px 24px; border-bottom: 1px solid #1f2a26; display: flex; align-items: center; gap: 12px; }}
  .header h1 {{ font-size: 16px; font-weight: 600; letter-spacing: -0.3px; }}
  .header .tag {{ background: #1f2a26; color: #7d8a82; font-size: 11px; padding: 2px 8px; border-radius: 4px; }}
  .graph-panel {{ padding: 16px 24px; border-bottom: 1px solid #1f2a26; background: #131b18; }}
  .graph-panel .label {{ font-size: 11px; color: #7d8a82; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
  .main {{ display: flex; height: calc(100vh - 180px); }}
  .sidebar {{ width: 180px; flex-shrink: 0; border-right: 1px solid #1f2a26; padding: 16px; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }}
  .sidebar .section-label {{ font-size: 10px; color: #7d8a82; text-transform: uppercase; letter-spacing: 1px; margin-top: 8px; margin-bottom: 2px; }}
  .sidebar .stat {{ background: #1f2a26; border-radius: 6px; padding: 10px; text-align: center; }}
  .sidebar .stat .value {{ font-size: 20px; font-weight: 600; line-height: 1; }}
  .sidebar .stat .key {{ font-size: 10px; color: #7d8a82; margin-top: 4px; }}
  .sidebar .stat.perf .value {{ color: #60efb0; }}
  .feed {{ flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 6px; }}
  .call-card {{ background: #1f2a26; border-radius: 6px; padding: 10px 12px; }}
  .call-card .top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
  .call-card .condition {{ font-weight: 500; font-size: 13px; }}
  .call-card .meta {{ font-size: 11px; color: #7d8a82; margin-bottom: 4px; display: flex; justify-content: space-between; }}
  .call-card .summary {{ font-size: 11px; color: #b8c2bc; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .badge {{ font-size: 10px; padding: 2px 7px; border-radius: 4px; font-weight: 500; }}
  .badge.CRITICAL {{ background: oklch(0.7 0.15 25 / 0.2); color: oklch(0.7 0.15 25); }}
  .badge.URGENT {{ background: oklch(0.78 0.09 75 / 0.2); color: oklch(0.78 0.09 75); }}
  .badge.ROUTINE {{ background: oklch(0.78 0.06 165 / 0.2); color: oklch(0.78 0.06 165); }}
  .perf-tag {{ font-size: 10px; color: #60efb0; }}
  .empty {{ color: #7d8a82; text-align: center; padding: 48px 0; font-size: 13px; }}
</style>
</head>
<body>
<div class="header">
  <h1>FirstCall Admin</h1>
  <span class="tag" id="last-updated">Loading...</span>
</div>
<div class="graph-panel">
  <div class="label">Latency &amp; TTFT — last 24h (CloudWatch)</div>
  {graph_html}
</div>
<div class="main">
  <div class="sidebar">
    <div class="section-label">Performance</div>
    <div class="stat perf"><div class="value" id="avg-latency">—</div><div class="key">Avg Latency</div></div>
    <div class="stat perf"><div class="value" id="avg-ttft">—</div><div class="key">Avg TTFT</div></div>
    <div class="section-label">Calls</div>
    <div class="stat"><div class="value" id="total-calls">—</div><div class="key">Total</div></div>
    <div class="stat"><div class="value" id="critical-calls" style="color:oklch(0.7 0.15 25)">—</div><div class="key">Critical</div></div>
    <div class="stat"><div class="value" id="pct-911">—</div><div class="key">→ 911</div></div>
    <div class="stat"><div class="value" id="avg-duration">—</div><div class="key">Avg Duration</div></div>
  </div>
  <div class="feed" id="feed"><div class="empty">Loading calls...</div></div>
</div>
<script>
function fmt(ms) {{
  if (ms == null) return '—';
  return ms < 1000 ? Math.round(ms) + 'ms' : (ms/1000).toFixed(1) + 's';
}}
function fmtDur(s) {{
  if (s == null) return '—';
  return Math.floor(s/60) + 'm ' + (s % 60) + 's';
}}
function pct(n, d) {{
  return d === 0 ? '0%' : Math.round(n/d*100) + '%';
}}
async function refresh() {{
  try {{
    const res = await fetch('/admin/calls', {{
      headers: {{'Authorization': 'Basic ' + btoa('{credentials.username}:{credentials.password}')}}
    }});
    const calls = await res.json();

    const total = calls.length;
    const critical = calls.filter(c => c.severity === 'CRITICAL').length;
    const called911 = calls.filter(c => c.called_911).length;
    const latencies = calls.filter(c => c.avg_latency_ms).map(c => c.avg_latency_ms);
    const ttfts = calls.filter(c => c.avg_ttft_ms).map(c => c.avg_ttft_ms);
    const durations = calls.filter(c => c.duration_seconds).map(c => c.duration_seconds);

    document.getElementById('total-calls').textContent = total;
    document.getElementById('critical-calls').textContent = critical;
    document.getElementById('pct-911').textContent = pct(called911, total);
    document.getElementById('avg-latency').textContent = fmt(latencies.length ? latencies.reduce((a,b)=>a+b,0)/latencies.length : null);
    document.getElementById('avg-ttft').textContent = fmt(ttfts.length ? ttfts.reduce((a,b)=>a+b,0)/ttfts.length : null);
    document.getElementById('avg-duration').textContent = fmtDur(durations.length ? Math.round(durations.reduce((a,b)=>a+b,0)/durations.length) : null);

    const feed = document.getElementById('feed');
    if (calls.length === 0) {{
      feed.innerHTML = '<div class="empty">No calls yet — call the number to get started.</div>';
    }} else {{
      feed.innerHTML = calls.map(c => `
        <div class="call-card">
          <div class="top">
            <span class="condition">${{c.condition}}</span>
            <span class="badge ${{c.severity}}">${{c.severity}}</span>
          </div>
          <div class="meta">
            <span>${{fmtDur(c.duration_seconds)}} · ${{c.called_911 ? '🚨 911 called · ' : ''}}${{new Date(c.created_at).toLocaleString()}}</span>
            <span class="perf-tag">${{fmt(c.avg_latency_ms)}} latency · ${{fmt(c.avg_ttft_ms)}} TTFT</span>
          </div>
          ${{c.summary ? `<div class="summary">"${{c.summary}}"</div>` : ''}}
        </div>
      `).join('');
    }}

    document.getElementById('last-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  }} catch(e) {{
    document.getElementById('last-updated').textContent = 'Refresh failed';
  }}
}}
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""
