from __future__ import annotations

from io import BytesIO
import base64
import json
import os
import re
import subprocess
import time
import wave


SAMPLE_RATE = 16_000
CHANNELS = 1


def record_and_transcribe(groq_client) -> str:
    """Record one push-to-talk utterance and transcribe it with Groq Whisper."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "Voice input needs sounddevice. Install it with: "
            "python -m pip install -r requirements-voice.txt"
        ) from exc

    if groq_client is None:
        raise RuntimeError("Voice input needs GROQ_API_KEY for transcription.")

    chunks: list[bytes] = []
    def receive_audio(indata, _frames, _time_info, _status):
        chunks.append(bytes(indata))

    print("Recording. Speak now, then press Enter to stop.", flush=True)
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=receive_audio,
    ):
        input()

    if not chunks:
        raise RuntimeError("No audio was captured. Check your microphone.")

    audio = BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(chunks))
    audio.seek(0)

    return transcribe_wav(groq_client, audio.getvalue())


def transcribe_wav(groq_client, wav_bytes: bytes) -> str:
    return transcribe_audio(groq_client, wav_bytes, "audio/wav")


def transcribe_audio(groq_client, audio_bytes: bytes, mime_type: str) -> str:
    """Transcribe a user-initiated browser recording through the existing Groq Whisper client."""
    if groq_client is None:
        raise RuntimeError("Voice input needs GROQ_API_KEY for transcription.")
    mime_to_extension = {
        "audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
    }
    extension = mime_to_extension.get(mime_type.split(";", 1)[0].strip().lower())
    if not extension:
        raise ValueError("Unsupported audio format from browser recording.")
    result = groq_client.audio.transcriptions.create(
        file=(f"dexter_voice.{extension}", audio_bytes, mime_type),
        model="whisper-large-v3-turbo",
        response_format="json",
    )
    transcript = (getattr(result, "text", "") or "").strip()
    if not transcript:
        raise RuntimeError("Groq could not recognize any speech.")
    return transcript


_WINDOWS_PATH_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:\\|\\\\)[^\s`<>\"'|,;]+")


def _spoken_paths(text: str, request: str) -> str:
    """Keep one requested/exact path; summarize long lists by drive and root."""
    paths = []
    for match in _WINDOWS_PATH_RE.finditer(text):
        path = match.group(0).rstrip(".?!:)]}")
        if path and path.casefold() not in {p.casefold() for p in paths}:
            paths.append(path)
    if not paths:
        return text

    request_lower = request.casefold()
    exact_requested = any(term in request_lower for term in (
        "exact path", "full path", "complete path", "where exactly", "exact location"
    ))
    if len(paths) == 1 or exact_requested:
        return text

    groups: dict[str, list[str]] = {}
    for path in paths:
        drive, remainder = path.split(":", 1)
        pieces = [piece for piece in re.split(r"[\\/]+", remainder) if piece]
        root = pieces[0] if pieces else "root"
        location = f"{drive.upper()} drive, {root} folder" if pieces else f"{drive.upper()} drive root"
        trailing = pieces[1:]
        label = "\\".join(trailing[-2:]) if trailing else root
        groups.setdefault(location, []).append(label)

    descriptions = []
    for location, labels in groups.items():
        shown = labels[:7]
        suffix = f", and {len(labels) - len(shown)} more" if len(labels) > len(shown) else ""
        descriptions.append(f"{location}: {', '.join(shown)}{suffix}")
    prefix = text[:_WINDOWS_PATH_RE.search(text).start()]
    prefix = re.sub(r"[`*_#>|\s-]+$", "", prefix).strip()
    lead = prefix.rstrip(" :;,.!") if prefix and len(prefix) < 100 else ""
    lead = lead or f"Found {len(paths)} items"
    return f"{lead}. {'; '.join(descriptions)}. Full paths are shown in the terminal."


def _voice_for_provider(provider: str) -> str:
    """Choose separate Windows voices for Groq and Gemini, with env overrides."""
    if "gemini" in provider.casefold():
        return os.getenv("DEVIL_GEMINI_VOICE", "Microsoft Zira Desktop")
    return os.getenv("DEVIL_GROQ_VOICE", "Microsoft David Desktop")


def speak_reply(
    text: str, request: str = "", wake_listener=None, provider: str = ""
) -> bool:
    """Speak a reply; return True if the user barged in with the wake word."""
    text = _spoken_paths(text, request)
    # Remove common Markdown/source clutter before reading aloud.
    spoken = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    spoken = re.sub(r"https?://\S+", "", spoken)
    spoken = re.sub(r"[`*_#>|]", "", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    if not spoken:
        return False
    # Keep long research answers responsive; the full answer remains on screen.
    if len(spoken) > 1200:
        spoken = spoken[:spoken.rfind(" ", 0, 1200)].rstrip(" ,;:")
        spoken += ". The full answer is shown on screen."

    payload = json.dumps({"text": spoken, "voice": _voice_for_provider(provider)})
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "try { "
        "Add-Type -AssemblyName System.Speech; "
        "$encoded = [Console]::In.ReadToEnd(); "
        "$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)) | ConvertFrom-Json; "
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.Volume = 100; $voice.Rate = 3; "
        "try { $voice.SelectVoice([string]$payload.voice) } catch { } "
        "$voice.Speak([string]$payload.text); $voice.Dispose() "
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    )
    monitor = wake_listener.start_barge_in_monitor() if wake_listener else None
    process = None
    interrupted = False
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            process.stdin.write(encoded)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            process.stdin = None

        while process.poll() is None:
            if monitor and monitor["detected"].wait(0.05):
                interrupted = True
                process.terminate()
                break
            if time.monotonic() - started > 60:
                process.terminate()
                raise RuntimeError("Windows speech output timed out.")
        if monitor and monitor["detected"].is_set():
            interrupted = True
        try:
            _, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
        if not interrupted and process.returncode:
            detail = (stderr or "").strip()
            raise RuntimeError(detail or "Windows speech output failed.")
    except OSError as exc:
        raise RuntimeError("Could not start Windows speech output.") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if monitor:
            wake_listener.stop_barge_in_monitor(monitor)
    return interrupted
