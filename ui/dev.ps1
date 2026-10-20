$ErrorActionPreference = 'Stop'

$runtime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$nodeBin = Join-Path $runtime 'node\bin'
$pnpm = Join-Path $runtime 'bin\fallback\pnpm.cmd'

if (-not (Test-Path (Join-Path $nodeBin 'node.exe'))) {
    throw "Codex-bundled Node.js was not found at '$nodeBin'. Install Node.js and pnpm, or update this script to their locations."
}
if (-not (Test-Path $pnpm)) {
    throw "Codex-bundled pnpm was not found at '$pnpm'. Install pnpm or update this script to its location."
}

$env:PATH = "$nodeBin;$env:PATH"
& $pnpm dev
exit $LASTEXITCODE
