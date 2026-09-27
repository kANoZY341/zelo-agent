"""Local-only FastAPI speech adapter for Zelo push-to-talk."""

from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from voice_service.providers import detect_tts_language, get_synthesizer, get_transcriber

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_TTS_CHARS = 4000

app = FastAPI(title="Zelo Voice Adapter", docs_url=None, redoc_url=None)


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    language: str = Field(default="auto", pattern="^(auto|en|ar)$")


def _required_token() -> str:
    token = os.getenv("ZELO_VOICE_TOKEN", "")
    if not token:
        raise RuntimeError("ZELO_VOICE_TOKEN is required")
    return token


def _authorize(authorization: str | None) -> None:
    prefix = "Bearer "
    supplied = authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, _required_token()):
        raise HTTPException(status_code=401, detail="voice_unauthorized")


@app.get("/health")
def health(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _authorize(authorization)
    return {"status": "healthy"}


@app.post("/transcribe")
async def transcribe(
    request: Request,
    authorization: str | None = Header(default=None),
    x_audio_type: str | None = Header(default=None),
) -> dict[str, object]:
    _authorize(authorization)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="voice_audio_too_large")

    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="voice_audio_empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="voice_audio_too_large")

    content_type = x_audio_type or request.headers.get("content-type", "application/octet-stream")
    try:
        result = get_transcriber().transcribe(audio, content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="voice_transcription_failed") from exc

    if not str(result.get("text", "")).strip():
        raise HTTPException(status_code=422, detail="voice_no_speech_detected")
    return result


@app.post("/synthesize")
def synthesize(
    payload: SynthesisRequest,
    authorization: str | None = Header(default=None),
) -> Response:
    _authorize(authorization)
    language = detect_tts_language(payload.text) if payload.language == "auto" else payload.language
    try:
        audio = get_synthesizer().synthesize(payload.text, language)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="voice_synthesis_failed") from exc

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Zelo-Voice-Language": language,
        },
    )
