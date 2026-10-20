import html
import re
import shutil
import textwrap

from devil import pathindex, policy
from devil.agent import DevilAgent
from devil.indexer import index_folder
from devil.jobs import manager as index_jobs, index_projects
from devil.voice import record_and_transcribe, speak_reply
from devil.wakeword import WakeTranscriptionError, WakeWordListener


def format_terminal_answer(answer: str) -> str:
    """Turn common Markdown in model replies into readable terminal text."""
    answer = html.unescape(answer)
    answer = re.sub(r"<br\s*/?>", "; ", answer, flags=re.IGNORECASE)
    answer = answer.replace("【", "").replace("】", "")

    # Render Markdown tables as labeled text rows, which wrap much better in a CLI.
    lines = answer.splitlines()
    rendered: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not cells or all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    i += 1
                    continue
                block.append(cells)
                i += 1
            if block:
                headers = block[0]
                for row in block[1:]:
                    rendered.append("")
                    for column, value in zip(headers, row):
                        if value:
                            rendered.append(f"{column}: {value}")
                rendered.append("")
            continue
        rendered.append(line)
        i += 1
    answer = "\n".join(rendered)

    # Keep a link's useful title and destination; strip markup syntax.
    answer = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)?",
        lambda m: f"{m.group(1)} ({m.group(2)})",
        answer,
    )
    answer = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), answer)
    answer = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)",
                    lambda m: m.group(1) or m.group(2), answer)
    answer = re.sub(r"^\s{0,3}#{1,6}\s*", "", answer, flags=re.MULTILINE)

    width = max(60, min(shutil.get_terminal_size((100, 24)).columns - 2, 120))
    wrapped = []
    blank_pending = False
    for line in answer.splitlines():
        line = line.strip()
        if not line:
            blank_pending = bool(wrapped)
            continue
        if blank_pending:
            wrapped.append("")
        wrapped.append(textwrap.fill(line, width=width, break_long_words=False,
                                     break_on_hyphens=False))
        blank_pending = False
    return "\n".join(wrapped)


def ends_with_question(answer: str) -> bool:
    """Recognize a final question even when the model wraps it in Markdown."""
    normalized = answer.rstrip().rstrip("*_`\"'”’)]} ")
    return normalized.endswith("?")


def voice_control_command(message: str) -> str | None:
    """Normalize common wake-word/ASR variants for local voice controls."""
    normalized = re.sub(r"[^a-z0-9]+", " ", message.casefold()).strip()
    normalized = re.sub(r"^(?:hey\s+)?(?:the\s+)?devil\s*", "", normalized)
    normalized = re.sub(r"^(?:please\s+)?", "", normalized)
    if normalized in {"exit", "quit", "stop", "shutdown", "shut down", "close"}:
        return "exit"
    if normalized in {
        "stop listening", "stop listen", "stop listening now",
        "switch to typing", "keyboard mode", "go to typing",
    }:
        return "stop_listening"
    return None


def main():
    print("=" * 48)
    print(" DEVIL | Your personal AI")
    print("=" * 48)
    print("Groq: technical tasks")
    print("Gemini: general conversation")
    print("Web search: OpenRouter/Nemotron (Groq fallback)")
    print("Wake word: say DEVIL, then speak after the beep.")
    print("Pause for 1.25 seconds to send your command; Ctrl+C switches to typing.")
    print("Say 'Devil stop listening' for keyboard mode or 'Devil exit' to quit.")
    print("Type /exit to quit, /help for commands.\n")

    agent = DevilAgent()

    if agent.groq is None:
        print("Warning: Groq API key not detected.")
    if agent.gemini is None:
        print("Warning: Gemini API key not detected.")
    if agent.openrouter is None:
        print("Tip: set OPENROUTER_API_KEY to enable Nemotron web research.")
    if not pathindex.is_built():
        print("Tip: run /scan once to build the PC-wide file-name index.")

    wake_listener = None
    wake_mode = False
    followup_mode = False
    if agent.groq is not None:
        try:
            wake_listener = WakeWordListener()
            wake_mode = True
        except Exception as exc:
            print(f"Wake-word mode unavailable: {exc}")
            print("Use /voice for push-to-talk or type normally.")

    while True:
        voice_turn = False
        if followup_mode:
            try:
                message = wake_listener.listen_for_reply(agent.groq)
                print(f"\nYou (voice) > {message}")
                voice_turn = True
            except KeyboardInterrupt:
                print("\nFollow-up listening cancelled.")
                followup_mode = False
                continue
            except WakeTranscriptionError as exc:
                print(f"\n{exc} Returning to wake-word listening.")
                followup_mode = False
                continue
            except Exception as exc:
                if str(exc) == "No follow-up reply heard.":
                    print("\nNo reply heard. Returning to wake-word listening.")
                else:
                    print(f"\nFollow-up listening stopped: {type(exc).__name__}: {exc}")
                followup_mode = False
                continue
        elif wake_mode:
            try:
                message = wake_listener.wait_for_command(agent.groq)
                print(f"\nYou (voice) > {message}")
                voice_turn = True
            except KeyboardInterrupt:
                print("\nWake listening paused. Type /wake to resume.")
                wake_mode = False
                continue
            except WakeTranscriptionError as exc:
                print(f"\n{exc} Listening for Devil again.")
                continue
            except Exception as exc:
                if str(exc) == "No command heard after the wake word.":
                    print(f"\n{exc} Listening for Devil again.")
                    continue
                print(f"\nWake listening stopped: {type(exc).__name__}: {exc}")
                wake_mode = False
                continue
        else:
            try:
                message = input("\nYou > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nDEVIL > Shutting down.")
                break

        if not message:
            continue

        if message.lower() == "/voice":
            try:
                message = record_and_transcribe(agent.groq)
                print(f"You (voice) > {message}")
                voice_turn = True
            except KeyboardInterrupt:
                print("\nVoice input cancelled.")
                continue
            except Exception as exc:
                print(f"Voice input failed: {type(exc).__name__}: {exc}")
                continue

        control = voice_control_command(message)
        if message.lower() in {"/exit", "/quit"} or control == "exit":
            print("DEVIL > Session ended.")
            break

        if control == "stop_listening" and (wake_mode or followup_mode):
            wake_mode = False
            followup_mode = False
            print("Wake listening paused. Type /wake to resume.")
            continue

        if message.lower() in {"/wake", "/listen"}:
            if wake_listener is None:
                try:
                    wake_listener = WakeWordListener()
                except Exception as exc:
                    print(f"Wake-word mode unavailable: {exc}")
                    continue
            wake_mode = True
            continue

        if message.lower() == "/roots":
            print("\nDEVIL may look only inside:")
            for root in policy.load_roots():
                print(f"  {root}")
            print(f"Edit {policy.CONFIG_PATH} to change this.")
            continue

        if message.lower() == "/scan":
            started = index_jobs.start("filename scan", lambda cancel, progress:
                                       pathindex.scan(on_progress=progress, cancel_event=cancel))
            print(started)
            continue

        if message.lower() == "/index-all":
            if not pathindex.is_built():
                print("Run /scan first so DEVIL can discover project roots.")
                continue
            print(index_jobs.start("project content indexing", index_projects))
            continue

        if message.lower() == "/jobs":
            print(index_jobs.snapshot() or "No indexing job has been started.")
            continue

        if message.lower() == "/cancel-index":
            print(index_jobs.cancel())
            continue

        if message.lower() == "/undo":
            from devil.file_ops import undo_last
            print(undo_last())
            continue

        if message.lower().startswith("/index"):
            folder = message[6:].strip().strip('"')
            if not folder:
                print("Usage: /index <folder>")
                continue
            try:
                if not policy.is_allowed(folder, is_dir=True):
                    print("That folder is outside the allowed roots (see /roots).")
                    continue
                print(index_jobs.start(f"content index: {folder}",
                                       lambda cancel, progress: index_folder(
                                           folder, cancel_event=cancel, on_progress=progress)))
            except Exception as exc:
                print(f"Index failed: {type(exc).__name__}: {exc}")
            continue

        if message.lower() == "/help":
            print(
                "\nCommands:\n"
                "  /help          Show commands\n"
                "  /scan          Build/refresh the PC-wide file-name index\n"
                "  /index <dir>   Index one folder's contents for code search\n"
                "  /index-all     Index useful text in discovered projects (30k-file cap)\n"
                "  /jobs          Show indexing progress/status\n"
                "  /cancel-index  Cancel the active indexing job\n"
                "  /undo          Undo the most recent recorded file change\n"
                "  /voice         Speak one message using your microphone\n"
                "  /wake          Resume listening for the Devil wake word\n"
                "  /roots         Show the folders DEVIL may look in\n"
                "  /exit          Quit DEVIL\n\n"
                "File writes, edits, recoverable deletions, ZIP creation, extraction,\n"
                "and undo require you to type YES at the approval prompt.\n\n"
                "Ask naturally. Technical requests are routed to Groq;\n"
                "general conversation is routed to Gemini. Web research and\n"
                "current-information requests use OpenRouter/Nemotron web search "
                "with Groq as fallback. Set DEVIL_OPENROUTER_MODEL to choose "
                "another OpenRouter model."
            )
            continue

        provider, answer = agent.ask(message, concise=voice_turn)
        print(f"\nDEVIL [{provider}] >\n{format_terminal_answer(answer)}")
        if voice_turn:
            try:
                interrupted = speak_reply(
                    answer, request=message, wake_listener=wake_listener,
                    provider=provider,
                )
            except Exception as exc:
                print(f"Voice reply unavailable: {exc}")
                interrupted = False
            if interrupted:
                print("\nWake word detected. Listening for your command.")
                wake_mode = True
                followup_mode = False
                continue
            # Listen without a wake word only when this reply asks another
            # question. Clear a previous follow-up state after final answers.
            followup_mode = bool(
                wake_listener is not None and ends_with_question(answer)
            )


if __name__ == "__main__":
    main()
