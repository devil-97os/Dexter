# DEVIL / Dexter UI architecture

## Inspected baseline

- Python CLI (`main.py`) owns the existing Groq technical/tool loop, Gemini general chat, OpenRouter/Nemotron web search, SQLite filename/content indexes, file-operation safeguards, and Vosk/Groq/Windows SAPI voice pipeline.
- Existing API models default to `openai/gpt-oss-20b` (Groq), `gemini-3.5-flash-lite` (Gemini), and `nvidia/nemotron-3.5-lightning` (OpenRouter). Environment overrides already exist. Credential presence is checked without reading or printing values.
- The current assistant history is in memory only. There is no GUI, HTTP backend, persistent owner memory, training dataset/process, training telemetry, or model-training code. The local model asset is Vosk wake-word recognition, not an LLM.
- Node 24 and pnpm 11 are available; npm is not on PATH. Python 3.13 has the existing model SDKs, Vosk, and sounddevice; FastAPI/Flask/uvicorn/PyTorch/Transformers are absent. The machine reports Radeon 780M and NVIDIA RTX 5060 Laptop GPUs, but no installed training runtime exists to use them.

## Decision

Use React + TypeScript + Vite, with Three.js for a live procedural android-face prototype. Keep Python and all credentials, file access, model calls, voice processing, and approval gates on the backend. Add a small loopback-only `ThreadingHTTPServer` API using the standard library rather than adding an ASGI dependency. Vite proxies `/api` during development; the Python server serves the production build. Keep existing SQLite indexes untouched and add a separate `data/dexter.db` for conversation history, owner-approved memories, explicit training candidates, and operational events.

The face will be procedural geometry because no suitable rigged character asset exists in the project. Its state comes from real backend/chat/voice events; it is a stylized prototype, not photorealistic or lip-synced. Nemotron is presented as web research, not as a general chat model. Training status will report the real absence of a trainer and metrics.

## Main risks and boundaries

- The local API can access the same allowed files and credentials as DEVIL. It must bind only to loopback, reject non-local hosts/origins, never return credential values, and route file-operation confirmation through an explicit UI approval prompt.
- Existing model calls are blocking and non-streaming. Run them in request threads and expose clear loading/error states; do not imply token streaming or provider cancellation.
- Existing local voice output uses Windows SAPI and speech input uses Groq Whisper. Browser capture is push-to-talk only; transcription is sent to the local backend for Whisper, with editable text before sending. Browser UI status should track actual request/playback state.
- Owner memory and training examples are separate, explicit, reviewable stores. Nothing is automatically added to either store, and no model training starts on launch.
- A new frontend toolchain introduces dependencies but does not replace or migrate the Python application or existing databases.
