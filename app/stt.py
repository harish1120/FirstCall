import asyncio
import os

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types import ListenV2CloseStream, ListenV2TurnInfo


async def transcribe_stream(audio_queue: asyncio.Queue, on_transcript, on_speech_start):
    deepgram = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

    try:
        print("[STT] Connecting to Deepgram...")
        async with deepgram.listen.v2.connect(
            model="flux-general-en",
            encoding="mulaw",
            sample_rate="8000",
            eot_timeout_ms="1000",
        ) as connection:
            print("[STT] Deepgram connected")

            responded_turns: set[int] = set()

            async def on_message(message):
                print(f"[STT] on_message fired: type={type(message).__name__} raw={message}")
                if isinstance(message, ListenV2TurnInfo):
                    transcript = message.transcript
                elif isinstance(message, dict):
                    transcript = message.get("transcript", "")
                else:
                    transcript = getattr(message, "transcript", "")

                event = getattr(message, "event", None) or (
                    message.get("event") if isinstance(message, dict) else None
                )
                eot_confidence = (
                    message.get("end_of_turn_confidence", 0) if isinstance(message, dict) else 0
                )
                turn_index = message.get("turn_index", -1) if isinstance(message, dict) else -1
                print(
                    f"[STT] transcript={repr(transcript)} event={repr(event)} eot_conf={eot_confidence:.2f}"
                )
                if event == "StartOfTurn":
                    await on_speech_start()
                is_end_of_turn = event == "EndOfTurn"
                is_high_confidence_final = event == "Update" and eot_confidence >= 0.5
                if (
                    transcript
                    and turn_index not in responded_turns
                    and (is_end_of_turn or is_high_confidence_final)
                ):
                    responded_turns.add(turn_index)
                    print(f"[STT] Firing on_transcript for turn {turn_index}: {repr(transcript)}")
                    await on_transcript(transcript)

            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, lambda e: print(f"[STT] Deepgram error: {e}"))

            async def send_audio():
                chunks_sent = 0
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        print(f"[STT] send_audio done — sent {chunks_sent} chunks")
                        await connection.send_close_stream(ListenV2CloseStream(type="CloseStream"))
                        break
                    chunks_sent += 1
                    if chunks_sent == 1:
                        print("[STT] First audio chunk sent to Deepgram")
                    await connection.send_media(chunk)

            asyncio.create_task(send_audio())
            print("[STT] start_listening...")
            await connection.start_listening()
            print("[STT] start_listening returned")

    except Exception as e:
        print(f"[STT] Could not open Deepgram socket: {e}")


if __name__ == "__main__":

    async def test():
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def on_transcript(text):
            print(f"Transcript: {text}")

        await queue.put(b"\x00" * 320)
        await queue.put(None)

        async def on_speech_start():
            pass

        await transcribe_stream(queue, on_transcript, on_speech_start)

    asyncio.run(test())
