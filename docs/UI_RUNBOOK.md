# DEVIL / Dexter UI runbook

## Architecture

The existing DEVIL Python CLI, provider clients, voice services, file index, search, and approval-protected file tools remain the system of record. The new React/TypeScript/Vite interface talks to a loopback-only Python `ThreadingHTTPServer` adapter in `web_server.py`. Provider credentials remain in Python. Conversations, owner-approved memories, training candidates, settings, and UI audit events use a separate `data/dexter.db`; the existing DEVIL databases are not migrated. The central avatar is a procedural Three.js android prototype, not a photorealistic character asset.

## Development

The normal commands below assume `pnpm` is on `PATH`. In this machine's current setup, pnpm is bundled with Codex and is not on the regular PowerShell `PATH`. Use this fallback path from PowerShell if `pnpm` is unrecognized:

```powershell
$runtime = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies"
$env:PATH = "$runtime\node\bin;$env:PATH"
$pnpm = "$runtime\bin\fallback\pnpm.cmd"
& $pnpm install
& $pnpm dev
```

Or, from `W:\Devil\ui`, run `.\dev.ps1` to start Vite with the bundled Node runtime on `PATH`.

The Codex-bundled pnpm wrapper uses its bundled Node executable, but scripts such as Vite also need that Node directory on `PATH`. If Node and pnpm are both already on `PATH`, the shorter commands below work. Install frontend packages once:

```powershell
cd W:\Devil\ui
pnpm install
```

Start the Python API in one PowerShell terminal:

```powershell
cd W:\Devil
.\.venv\Scripts\python.exe web_server.py
```

Start Vite in a second terminal:

```powershell
cd W:\Devil\ui
pnpm dev
```

Open `http://127.0.0.1:5173`.

## Production-style local launch

```powershell
cd W:\Devil\ui
pnpm build
cd W:\Devil
.\.venv\Scripts\python.exe web_server.py
```

Open `http://127.0.0.1:8765`; the Python server serves the built frontend and local API.

## Regression checks

```powershell
cd W:\Devil
.\.venv\Scripts\python.exe -m compileall -q web_server.py devil
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd W:\Devil\ui
pnpm build
```

Voice in the browser uses user-activated push-to-talk and Groq Whisper; it needs a microphone permission and configured Groq credential. Speech output uses Windows SAPI. No live microphone, speaker, or remote provider interaction is performed by the regression suite.
