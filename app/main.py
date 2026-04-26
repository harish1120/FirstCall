from fastapi import FastAPI, Request, status, Depends
from fastapi.responses import Response
from app.agent import build_response, get_session_meta
from app.database import Base, engine, get_db
from app import models
from fastapi.responses import StreamingResponse
from app.tts import text_to_speech_stream

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FirstCall")

PENDING_AUDIO = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/voice")
async def handle_call(request: Request):
    """Twilio calls this endpoint when someone dials the FirstCall number."""
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say voice="Polly.Joanna">
            Hello, this is FirstCall. Please describe the emergency!.
        </Say>
        <Gather input="speech" action="https://caterer-divorcee-unloving.ngrok-free.dev/handle-speech" speechTimeout="auto">
        </Gather>
    </Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.post("/handle-speech")
async def handle_speech(request: Request):
    form = await request.form()
    transcript = form.get("SpeechResult")
    call_sid = form.get("CallSid")

    if not transcript:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say>Sorry, I didn't catch that. Please describe the emergency.</Say>
            <Gather input="speech" action="https://caterer-divorcee-unloving.ngrok-free.dev/handle-speech" speechTimeout="auto">                                                                                                          
            </Gather>
        </Response>
        """
        return Response(content=twiml, media_type="application/xml")

    response = build_response(transcript, call_sid)
    PENDING_AUDIO[call_sid] = response

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>https://caterer-divorcee-unloving.ngrok-free.dev/audio/{call_sid}</Play>
        <Gather input="speech" action="https://caterer-divorcee-unloving.ngrok-free.dev/handle-speech" speechTimeout="auto">
        </Gather>
    </Response>
    """
    return Response(content=twiml, media_type="application/xml")


@app.get("/audio/{call_sid}")
async def audio_stream(call_sid: str):
    text = PENDING_AUDIO.pop(call_sid, None)
    if not text:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return StreamingResponse(text_to_speech_stream(text), media_type="audio/mpeg")


@app.post("/handle-recording")
async def handle_recording(request: Request):
    """Receives the recorded audio URL from Twilio after the caller speaks."""
    form = await request.form()
    recording_url = form.get("RecordingUrl")
    # available if using <Gather> instead
    transcript = form.get("SpeechResult")

    # TODO: pipe audio through Deepgram STT, then Claude agent
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>Thank you. Processing your request.</Say>
    </Response>"""
    return Response(content=twiml, media_type="application/xml")


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

    return {"status": "logged"}
