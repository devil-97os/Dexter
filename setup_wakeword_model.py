from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import urllib.request
import zipfile


URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR = Path(__file__).resolve().parent / "data" / "models"
ARCHIVE = MODEL_DIR / "vosk-model-small-en-us-0.15.zip"
MODEL_NAME = "vosk-model-small-en-us-0.15"
PARTS = 4


def fetch_range(start: int, end: int, index: int, expected_total: int) -> Path:
    part = MODEL_DIR / f".{MODEL_NAME}.part{index}"
    if part.exists() and part.stat().st_size == end - start + 1:
        return part
    request = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 206:
            raise RuntimeError("The model server did not honor a ranged download.")
        content_range = response.headers.get("Content-Range", "")
        if not content_range.startswith(f"bytes {start}-{end}/{expected_total}"):
            raise RuntimeError(f"Unexpected model download range: {content_range}")
        with part.open("wb") as output:
            shutil.copyfileobj(response, output)
    if part.stat().st_size != end - start + 1:
        raise RuntimeError(f"Model download segment {index} was incomplete.")
    return part


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / MODEL_NAME
    if (model_path / "am").is_dir():
        print(f"Wake-word model is already installed: {model_path}")
        return

    request = urllib.request.Request(URL, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        total_size = int(response.headers["Content-Length"])
        if response.headers.get("Accept-Ranges", "").lower() != "bytes":
            raise RuntimeError("Model server does not support resumable downloads.")

    prefix_size = ARCHIVE.stat().st_size if ARCHIVE.exists() else 0
    if prefix_size > total_size:
        ARCHIVE.unlink()
        prefix_size = 0
    remaining = total_size - prefix_size
    if remaining:
        segment_size = (remaining + PARTS - 1) // PARTS
        ranges = []
        for index in range(PARTS):
            start = prefix_size + index * segment_size
            if start >= total_size:
                break
            end = min(total_size - 1, start + segment_size - 1)
            ranges.append((start, end, index))

        print(f"Downloading offline wake-word model ({total_size // 1_000_000} MB)...")
        with ThreadPoolExecutor(max_workers=PARTS) as pool:
            parts = list(pool.map(
                lambda item: fetch_range(item[0], item[1], item[2], total_size),
                ranges,
            ))
        with ARCHIVE.open("ab") as output:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output)
                part.unlink()

    if ARCHIVE.stat().st_size != total_size:
        raise RuntimeError("The wake-word model archive is incomplete.")

    root = MODEL_DIR.resolve()
    with zipfile.ZipFile(ARCHIVE) as archive:
        for info in archive.infolist():
            target = (root / info.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError("The model archive contains an unsafe path.")
        archive.extractall(root)

    if not (model_path / "am").is_dir():
        raise RuntimeError("The downloaded archive did not contain the expected model.")
    print(f"Wake-word model installed: {model_path}")


if __name__ == "__main__":
    main()
