"""Local-only FastAPI speech adapter for Zelo push-to-talk."""

from __future__ import annotations

import asyncio
import hmac
import os
from time import monotonic

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from voice_service.providers import detect_tts_language, get_transcriber
from voice_service.synthesis_worker import SynthesisWorker, SynthesisWorkerBusy

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_TTS_CHARS = 4000
DEFAULT_TTS_DEADLINE_SECONDS = 110.0
SYNTHESIS_POLL_SECONDS = 0.05

app = FastAPI(title="Zelo Voice Adapter", docs_url=None, redoc_url=None)
_synthesis_worker = SynthesisWorker()


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    language: str = Field(default="auto", pattern="^(auto|en|ar)$")


def _required_token() -> str:
    token = os.getenv("ZELO_VOICE_TOKEN", "")
    if not token:
        raise RuntimeError("ZELO_VOICE_TOKEN is required")
    if (
        not 32 <= len(token) <= 512
        or not token.isascii()
        or any(character.isspace() for character in token)
    ):
        raise RuntimeError("ZELO_VOICE_TOKEN must contain 32-512 non-whitespace ASCII characters")
    return token


def _tts_deadline_seconds() -> float:
    raw = os.getenv("ZELO_TTS_DEADLINE_SECONDS", str(DEFAULT_TTS_DEADLINE_SECONDS))
    try:
        deadline = float(raw)
    except ValueError as exc:
        raise RuntimeError("ZELO_TTS_DEADLINE_SECONDS must be numeric") from exc
    if not 1.0 <= deadline <= 600.0:
        raise RuntimeError("ZELO_TTS_DEADLINE_SECONDS must be between 1 and 600")
    return deadline


def _authorize(authorization: str | None) -> None:
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix) :]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not supplied or not hmac.compare_digest(supplied, _required_token()):
        raise HTTPException(status_code=401, detail="voice_unauthorized")


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="voice_audio_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


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
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="voice_content_length_invalid",
            ) from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="voice_content_length_invalid")
        if declared_length > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="voice_audio_too_large")

    audio = await _read_bounded_body(request, MAX_AUDIO_BYTES)
    if not audio:
        raise HTTPException(status_code=400, detail="voice_audio_empty")

    content_type = x_audio_type or request.headers.get(
        "content-type",
        "application/octet-stream",
    )
    try:
        result = await run_in_threadpool(
            get_transcriber().transcribe,
            audio,
            content_type,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="voice_transcription_failed",
        ) from exc

    if not str(result.get("text", "")).strip():
        raise HTTPException(status_code=422, detail="voice_no_speech_detected")
    return result


@app.post("/synthesize")
async def synthesize(
    request: Request,
    payload: SynthesisRequest,
    authorization: str | None = Header(default=None),
) -> Response:
    _authorize(authorization)
    language = (
        detect_tts_language(payload.text)
        if payload.language == "auto"
        else payload.language
    )
    request_id: str | None = None
    completed = False

    try:
        request_id = _synthesis_worker.start(payload.text, language)
        deadline = monotonic() + _tts_deadline_seconds()

        while True:
            audio = _synthesis_worker.poll(request_id)
            if audio is not None:
                completed = True
                break
            if await request.is_disconnected():
                raise HTTPException(status_code=499, detail="voice_client_disconnected")
            if monotonic() >= deadline:
                raise HTTPException(status_code=503, detail="voice_synthesis_timeout")
            await asyncio.sleep(SYNTHESIS_POLL_SECONDS)
    except SynthesisWorkerBusy as exc:
        raise HTTPException(status_code=503, detail="voice_synthesis_busy") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except asyncio.CancelledError:
        if request_id is not None:
            _synthesis_worker.abort(request_id)
        raise
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="voice_synthesis_failed") from exc
    finally:
        if request_id is not None and not completed:
            _synthesis_worker.abort(request_id)

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Zelo-Voice-Language": language,
        },
    )
