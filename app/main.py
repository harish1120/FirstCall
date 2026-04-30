import asyncio
import base64
import json
import os
from fastapi import FastAPI, Request, WebSocket, status, Depends
from fastapi.responses import Response, StreamingResponse
from app.agent import build_response, get_session_meta, clear_session
from app.database import Base, engine, get_db
from app import models
from app.tts import text_to_speech_stream, intro_speech
from app.stt import transcribe_stream
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
from twilio.request_validator import RequestValidator

load_dotenv()

BASE_URL = os.getenv("BASE_URL")
validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
Base.metadata.create_all(bind=engine)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="FirstCall")

app.state.limiter = limiter

PENDING_AUDIO = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/voice")
@limiter.limit("5/minute")
async def handle_call(request: Request):
    """Twilio calls this endpoint when someone dials the FirstCall number."""
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    if not validator.validate(url, dict(form), signature):
        return Response(status_code=403)
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


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()

    audio_queue = asyncio.Queue()
    call_sid = None

    async def on_transcript(text):
        try:
            print(f"{country_code}")
            response = await asyncio.to_thread(build_response, text, call_sid, country_code)
            for chunk in text_to_speech_stream(response):
                await websocket.send_text(json.dumps({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": base64.b64encode(chunk).decode("utf-8")}
                }))
        except Exception as e:
            print(f"Error in on_transcript: {e}")

    asyncio.create_task(transcribe_stream(audio_queue, on_transcript))

    async for message in websocket.iter_text():
        data = json.loads(message)

        if data["event"] == "start":
            call_sid = data["start"]["callSid"]
            stream_sid = data["start"]["streamSid"]
            country_code = data["start"]["customParameters"].get(
                "country", "US")
        elif data["event"] == "media":
            audio = base64.b64decode(data["media"]["payload"])
            await audio_queue.put(audio)
        elif data["event"] == "stop":
            await audio_queue.put(None)
            break


@app.api_route("/call-status", methods=["GET", "POST"], status_code=status.HTTP_201_CREATED)
async def call_status(request: Request, db=Depends(get_db)):
    form = await request.form()
    call_sid = form.get("CallSid")
    duration_seconds = form.get("CallDuration")
    session_meta = get_session_meta(call_sid)

    if not session_meta:
        return {"status": "no session"}

    call_log = models.CallLog(call_sid=call_sid, duration_seconds=int(duration_seconds),
                              severity=session_meta['severity'], condition=session_meta['condition'])
    db.add(call_log)
    db.commit()
    clear_session(call_sid)
    return {"status": "logged"}

#################################################### V.1.0####################################################
# @app.get("/audio/{call_sid}")
# async def audio_stream(call_sid: str):
#     text = PENDING_AUDIO.pop(call_sid, None)
#     if not text:
#         return Response(status_code=status.HTTP_404_NOT_FOUND)
#     return StreamingResponse(text_to_speech_stream(text), media_type="audio/mpeg")

# @app.post("/handle-speech")
# async def handle_speech(request: Request):
#     form = await request.form()
#     transcript = form.get("SpeechResult")
#     call_sid = form.get("CallSid")

#     if not transcript:
#         twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
#         <Response>
#             <Say>Sorry, I didn't catch that. Please describe the emergency.</Say>
#             <Gather input="speech" action="{BASE_URL}/handle-speech" speechTimeout="auto">
#             </Gather>
#         </Response>
#         """
#         return Response(content=twiml, media_type="application/xml")

#     response = build_response(transcript, call_sid)
#     PENDING_AUDIO[call_sid] = response

#     twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
#     <Response>
#         <Play>{BASE_URL}/audio/{call_sid}</Play>
#         <Gather input="speech" action="{BASE_URL}/handle-speech" speechTimeout="auto">
#         </Gather>
#     </Response>
#     """
#     return Response(content=twiml, media_type="application/xml")


# @app.post("/handle-recording")
# async def handle_recording(request: Request):
#     """Receives the recorded audio URL from Twilio after the caller speaks."""
#     form = await request.form()
#     recording_url = form.get("RecordingUrl")
#     # available if using <Gather> instead
#     transcript = form.get("SpeechResult")

#     # TODO: pipe audio through Deepgram STT, then Claude agent
#     twiml = """<?xml version="1.0" encoding="UTF-8"?>
#     <Response>
#         <Say>Thank you. Processing your request.</Say>
#     </Response>"""
#     return Response(content=twiml, media_type="application/xml")
