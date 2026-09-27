#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON. Create the existing Zelo virtualenv first." >&2
  exit 1
fi

"$PYTHON" -m pip install -r requirements-voice.txt

ENV_FILE="$ROOT/.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
ZELO_ENV_FILE="$ENV_FILE" "$PYTHON" - <<'PY'
import os
import secrets
from pathlib import Path

path = Path(os.environ["ZELO_ENV_FILE"])
lines = path.read_text(encoding="utf-8").splitlines()
prefix = "ZELO_VOICE_SERVICE_TOKEN="
replacement = f'ZELO_VOICE_SERVICE_TOKEN="{secrets.token_urlsafe(48)}"'
found = False
changed = False

for index, line in enumerate(lines):
    if not line.startswith(prefix):
        continue
    found = True
    raw = line[len(prefix):].strip().strip('"')
    if not raw or raw == "REPLACE_WITH_A_RANDOM_LOCAL_VOICE_TOKEN":
        lines[index] = replacement
        changed = True

if not found:
    lines.extend(["", replacement])
    changed = True

if changed:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Generated ZELO_VOICE_SERVICE_TOKEN in .env")
PY

MODEL_ROOT="$ROOT/runtime/voice/models"
SUPER_DIR="$MODEL_ROOT/sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
mkdir -p "$MODEL_ROOT"

if [[ ! -f "$SUPER_DIR/duration_predictor.int8.onnx" ]]; then
  archive="$(mktemp --suffix=.tar.bz2)"
  trap 'rm -f "$archive"' EXIT
  curl -fL     "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2"     -o "$archive"
  tar -xjf "$archive" -C "$MODEL_ROOT"
  rm -f "$archive"
  trap - EXIT
fi

ZELO_ROOT="$ROOT" "$PYTHON" - <<'PY'
import os
from pathlib import Path
from faster_whisper import WhisperModel

root = Path(os.environ["ZELO_ROOT"])
download_root = root / "runtime" / "voice" / "models" / "faster-whisper"
download_root.mkdir(parents=True, exist_ok=True)
WhisperModel(
    "small",
    device="cpu",
    compute_type="int8",
    download_root=str(download_root),
)
print(f"faster-whisper small ready under {download_root}")
PY

echo "Zelo voice models are ready."
