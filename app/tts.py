
import os
from typing import Generator
# from io import BytesIO
from dotenv import load_dotenv
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
elevenlabs = ElevenLabs(
    api_key=ELEVENLABS_API_KEY,
)


def text_to_speech_stream(text: str) -> Generator[bytes, None, None]:
    # Perform the text-to-speech conversion
    response = elevenlabs.text_to_speech.stream(
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # Adam pre-made voice
        output_format="mp3_22050_32",
        text=text,
        model_id="eleven_turbo_v2",
        # Optional voice settings that allow you to customize the output
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=1.0,
            style=0.0,
            use_speaker_boost=True,
            speed=1.0,
        ),
    )

    # Create a BytesIO object to hold the audio data in memory
#     audio_stream = BytesIO()

    # Write each chunk of audio data to the stream
    for chunk in response:
        if chunk:
            yield chunk

    # Reset stream position to the beginning
#     audio_stream.seek(0)

#     # Return the stream for further use
#     return audio_stream

if __name__ == "__main__":
      with open("test_audio.mp3", "wb") as f:
          for chunk in text_to_speech_stream("This is a test of the FirstCall voice system."):
              f.write(chunk)
      print("saved to test_audio.mp3")