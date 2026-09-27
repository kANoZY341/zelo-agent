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
if ! grep -q '^ZELO_VOICE_SERVICE_TOKEN=' "$ENV_FILE"; then
  VOICE_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  printf '\nZELO_VOICE_SERVICE_TOKEN="%s"\n' "$VOICE_TOKEN" >> "$ENV_FILE"
  echo "Generated ZELO_VOICE_SERVICE_TOKEN in .env"
fi

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
