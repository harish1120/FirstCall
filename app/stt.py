import asyncio
import os

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v2.types import ListenV2CloseStream, ListenV2TurnInfo


async def transcribe_stream(audio_queue: asyncio.Queue, on_transcript):
    deepgram = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

    try:
        async with deepgram.listen.v2.connect(
            model="flux-general-en",
            encoding="mulaw",
            sample_rate="8000",
            eot_timeout_ms="1000",
        ) as connection:

            async def on_message(message):
                if isinstance(message, ListenV2TurnInfo):
                    transcript = message.transcript
                elif isinstance(message, dict):
                    transcript = message.get("transcript", "")
                else:
                    transcript = getattr(message, "transcript", "")

                # Only act on EndOfTurn for a complete utterance
                event = getattr(message, "event", None) or (
                    message.get("event") if isinstance(message, dict) else None
                )
                if transcript and event == "EndOfTurn":
                    await on_transcript(transcript)

            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, lambda e: print(f"Deepgram error: {e}"))

            async def send_audio():
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        await connection.send_close_stream(ListenV2CloseStream(type="CloseStream"))
                        break
                    await connection.send_media(chunk)

            asyncio.create_task(send_audio())
            await connection.start_listening()

    except Exception as e:
        print(f"Could not open Deepgram socket: {e}")


if __name__ == "__main__":

    async def test():
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def on_transcript(text):
            print(f"Transcript: {text}")

        await queue.put(b"\x00" * 320)
        await queue.put(None)

        await transcribe_stream(queue, on_transcript)

    asyncio.run(test())
