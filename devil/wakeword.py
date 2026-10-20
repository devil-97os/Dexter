from __future__ import annotations

from array import array
from collections import deque
from io import BytesIO
import json
from pathlib import Path
import re
from threading import Event, Thread
import wave

from .voice import transcribe_wav


SAMPLE_RATE = 16_000
BLOCK_FRAMES = 4_000  # 250 ms
MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "models" / "vosk-model-small-en-us-0.15"
MAX_WAKE_SEGMENT_BLOCKS = 48  # 12 seconds of audio before a Vosk endpoint
FOLLOWUP_SILENCE_BLOCKS = 5   # 1.25 seconds
FOLLOWUP_START_TIMEOUT_BLOCKS = 48  # 12 seconds
MAX_COMMAND_BLOCKS = 120      # 30 seconds
WAKE_GRAMMAR = json.dumps(["devil", "hey devil"])


class WakeTranscriptionError(RuntimeError):
    """A temporary Groq transcription failure that should not stop listening."""


def _rms(audio: bytes) -> float:
    samples = array("h")
    samples.frombytes(audio)
    if not samples:
        return 0.0
    return (sum(sample * sample for sample in samples) / len(samples)) ** 0.5


class WakeWordListener:
    """Local wake-word detection followed by silence-ended Groq transcription."""

    def __init__(self):
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as exc:
            raise RuntimeError(
                "Wake-word mode needs its audio packages. Install them with "
                "python -m pip install -r requirements-wakeword.txt"
            ) from exc
        if not (MODEL_PATH / "am").exists():
            raise RuntimeError(
                "The offline wake-word model is missing. Install the model "
                "by running python setup_wakeword_model.py from the DEVIL "
                "folder."
            )
        SetLogLevel(-1)
        self.sd = sd
        self.KaldiRecognizer = KaldiRecognizer
        self.model = Model(str(MODEL_PATH))
        self._pending_activation: tuple[list[bytes], str] | None = None

    def _new_wake_recognizer(self):
        """Constrain local recognition to the wake phrase for better recall."""
        try:
            return self.KaldiRecognizer(self.model, SAMPLE_RATE, WAKE_GRAMMAR)
        except Exception:
            # Older Vosk builds may not support runtime grammars.
            return self.KaldiRecognizer(self.model, SAMPLE_RATE)

    def start_barge_in_monitor(self) -> dict:
        """Listen locally for Devil while SAPI is speaking."""
        monitor = {"stop": Event(), "detected": Event(), "thread": None}
        self._pending_activation = None

        def listen() -> None:
            try:
                wake_recognizer = self._new_wake_recognizer()
                segment_recognizer = self.KaldiRecognizer(self.model, SAMPLE_RATE)
                segment_chunks: deque[bytes] = deque(maxlen=MAX_WAKE_SEGMENT_BLOCKS)
                with self.sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_FRAMES,
                    channels=1,
                    dtype="int16",
                ) as stream:
                    while not monitor["stop"].is_set():
                        audio, overflowed = stream.read(BLOCK_FRAMES)
                        chunk = bytes(audio)
                        if overflowed:
                            continue
                        segment_chunks.append(chunk)
                        wake_complete = wake_recognizer.AcceptWaveform(chunk)
                        wake_result = json.loads(
                            wake_recognizer.Result() if wake_complete
                            else wake_recognizer.PartialResult()
                        )
                        detected_text = wake_result.get(
                            "text" if wake_complete else "partial", ""
                        )
                        segment_complete = segment_recognizer.AcceptWaveform(chunk)
                        segment_result = json.loads(
                            segment_recognizer.Result() if segment_complete
                            else segment_recognizer.PartialResult()
                        )
                        segment_text = segment_result.get(
                            "text" if segment_complete else "partial", ""
                        )
                        if re.search(r"\bdevil\b", detected_text, flags=re.IGNORECASE):
                            self._pending_activation = (
                                list(segment_chunks), segment_text or detected_text.casefold()
                            )
                            monitor["detected"].set()
                            return
                        if segment_complete:
                            segment_chunks.clear()
            except Exception as exc:
                monitor["error"] = exc

        monitor["thread"] = Thread(target=listen, name="devil-barge-in", daemon=True)
        monitor["thread"].start()
        return monitor

    @staticmethod
    def stop_barge_in_monitor(monitor: dict) -> None:
        monitor["stop"].set()
        monitor["thread"].join(timeout=2)

    @staticmethod
    def _play_activation_beep() -> None:
        """Play a short two-tone cue through the selected Windows audio output."""
        try:
            import math

            samples = array("h")
            for frequency in (880, 1175):
                count = int(SAMPLE_RATE * 0.16)
                samples.extend(
                    int(9000 * math.sin(2 * math.pi * frequency * i / SAMPLE_RATE))
                    for i in range(count)
                )
                samples.extend([0] * int(SAMPLE_RATE * 0.05))
            with self.sd.RawOutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16"
            ) as output:
                output.write(samples.tobytes())
        except Exception:
            try:
                import winsound
                winsound.Beep(880, 140)
                winsound.Beep(1175, 180)
            except (ImportError, RuntimeError, OSError):
                print("\a", end="", flush=True)

    def wait_for_command(self, groq_client) -> str:
        if groq_client is None:
            raise RuntimeError("Wake-word voice input needs GROQ_API_KEY.")

        print("DEVIL is listening locally. Say 'Devil' to wake it.", flush=True)
        pending = self._pending_activation
        self._pending_activation = None
        if pending is not None:
            wake_chunks, wake_text = pending
        else:
            wake_recognizer = self._new_wake_recognizer()
            segment_recognizer = self.KaldiRecognizer(self.model, SAMPLE_RATE)
            # Preserve the current utterance so a command spoken right after
            # Devil is retained, while completed earlier utterances are dropped.
            segment_chunks: deque[bytes] = deque(maxlen=MAX_WAKE_SEGMENT_BLOCKS)
            with self.sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                channels=1,
                dtype="int16",
            ) as stream:
                while True:
                    audio, overflowed = stream.read(BLOCK_FRAMES)
                    chunk = bytes(audio)
                    if overflowed:
                        continue
                    segment_chunks.append(chunk)
                    wake_complete = wake_recognizer.AcceptWaveform(chunk)
                    wake_result = json.loads(
                        wake_recognizer.Result() if wake_complete
                        else wake_recognizer.PartialResult()
                    )
                    recognized = wake_result.get(
                        "text" if wake_complete else "partial", ""
                    )
                    segment_complete = segment_recognizer.AcceptWaveform(chunk)
                    segment_result = json.loads(
                        segment_recognizer.Result() if segment_complete
                        else segment_recognizer.PartialResult()
                    )
                    segment_text = segment_result.get(
                        "text" if segment_complete else "partial", ""
                    )
                    if re.search(r"\bdevil\b", recognized, flags=re.IGNORECASE):
                        wake_text = (segment_text or recognized).casefold()
                        wake_chunks = list(segment_chunks)
                        break
                    if segment_complete:
                        # Vosk has ended this utterance and reset for the next one.
                        segment_chunks.clear()

        print("I heard you. Speak after the beep; pause to finish.", flush=True)
        self._play_activation_beep()

        command_recognizer = self.KaldiRecognizer(self.model, SAMPLE_RATE)
        # Only the current, locally recognized Vosk utterance and audio recorded
        # after activation leave the machine. Completed earlier utterances stay
        # on-device and are discarded.
        chunks = wake_chunks
        for wake_chunk in wake_chunks:
            command_recognizer.AcceptWaveform(wake_chunk)

        # If words followed the wake word in the same utterance, the current
        # Vosk segment already contains the command; otherwise wait for speech.
        wake_match = re.search(r"\bdevil\b", wake_text)
        trailing_words = wake_text[wake_match.end():].strip() if wake_match else ""
        speech_started = bool(trailing_words)
        silence_blocks = 0
        no_speech_blocks = 0
        voice_threshold = 250
        max_blocks = MAX_COMMAND_BLOCKS

        with self.sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            channels=1,
            dtype="int16",
        ) as stream:
            for _ in range(max_blocks):
                audio, overflowed = stream.read(BLOCK_FRAMES)
                chunk = bytes(audio)
                if overflowed:
                    continue
                chunks.append(chunk)
                command_recognizer.AcceptWaveform(chunk)
                loud = _rms(chunk) >= voice_threshold

                if loud:
                    speech_started = True
                    silence_blocks = 0
                elif speech_started:
                    silence_blocks += 1
                else:
                    no_speech_blocks += 1

                if speech_started and silence_blocks >= FOLLOWUP_SILENCE_BLOCKS:
                    break
                if not speech_started and no_speech_blocks >= 32:  # 8 seconds
                    raise RuntimeError("No command heard after the wake word.")
            else:
                raise RuntimeError("Voice command exceeded the 30-second limit.")

        wav_data = BytesIO()
        with wave.open(wav_data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(b"".join(chunks))

        try:
            transcript = transcribe_wav(groq_client, wav_data.getvalue())
        except Exception as exc:
            raise WakeTranscriptionError(
                f"Groq voice transcription failed: {type(exc).__name__}: {exc}"
            ) from exc
        transcript = re.sub(r"^\s*(?:hey\s+)?devil\b[\s,.:;-]*", "", transcript,
                            flags=re.IGNORECASE).strip()
        if not transcript:
            raise RuntimeError("Wake word heard, but no command was recognized.")
        return transcript

    def listen_for_reply(self, groq_client) -> str:
        """Record one spoken reply without requiring the wake word."""
        if groq_client is None:
            raise RuntimeError("Voice input needs GROQ_API_KEY for transcription.")

        print("DEVIL asked a question. Listening for your reply now...", flush=True)
        chunks: list[bytes] = []
        preroll: deque[bytes] = deque(maxlen=2)
        speech_started = False
        silence_blocks = 0
        no_speech_blocks = 0

        with self.sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            channels=1,
            dtype="int16",
        ) as stream:
            for _ in range(MAX_COMMAND_BLOCKS):
                audio, overflowed = stream.read(BLOCK_FRAMES)
                chunk = bytes(audio)
                if overflowed:
                    continue
                loud = _rms(chunk) >= 250

                if loud:
                    if not speech_started:
                        chunks.extend(preroll)
                        speech_started = True
                    chunks.append(chunk)
                    silence_blocks = 0
                elif speech_started:
                    chunks.append(chunk)
                    silence_blocks += 1
                    if silence_blocks >= FOLLOWUP_SILENCE_BLOCKS:
                        break
                else:
                    preroll.append(chunk)
                    no_speech_blocks += 1
                    if no_speech_blocks >= FOLLOWUP_START_TIMEOUT_BLOCKS:
                        raise RuntimeError("No follow-up reply heard.")
            else:
                raise RuntimeError("Follow-up reply exceeded the 30-second limit.")

        wav_data = BytesIO()
        with wave.open(wav_data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(b"".join(chunks))

        try:
            transcript = transcribe_wav(groq_client, wav_data.getvalue())
        except Exception as exc:
            raise WakeTranscriptionError(
                f"Groq voice transcription failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not transcript:
            raise RuntimeError("No follow-up reply was recognized.")
        return transcript
