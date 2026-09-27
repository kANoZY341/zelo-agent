"""Authenticated loopback-only HTTP boundary for local speech recognition and synthesis."""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from voice_service.config import VoiceSettings
from voice_service.providers import STTProvider, TTSProvider, build_stt, build_tts


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=32000)
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")


def _authorized(request: Request, settings: VoiceSettings) -> bool:
    configured = settings.service_token
    if configured is None:
        return False
    expected = f"Bearer {configured.get_secret_value()}"
    supplied = request.headers.get("authorization", "")
    return hmac.compare_digest(supplied, expected)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "voice_service_auth_required"},
        headers={"Cache-Control": "no-store"},
    )


async def _bounded_body(
    request: Request,
    limit: int,
) -> tuple[bytes | None, JSONResponse | None]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            return None, JSONResponse(
                status_code=400,
                content={"detail": "invalid_content_length"},
            )
        if declared < 0:
            return None, JSONResponse(
                status_code=400,
                content={"detail": "invalid_content_length"},
            )
        if declared > limit:
            return None, JSONResponse(
                status_code=413,
                content={"detail": "request_too_large"},
            )

    collected = bytearray()
    async for chunk in request.stream():
        if len(collected) + len(chunk) > limit:
            return None, JSONResponse(
                status_code=413,
                content={"detail": "request_too_large"},
            )
        collected.extend(chunk)
    return bytes(collected), None


def create_voice_app(
    settings: VoiceSettings | None = None,
    *,
    stt: STTProvider | None = None,
    tts: TTSProvider | None = None,
) -> FastAPI:
    configuration = settings or VoiceSettings()
    application = FastAPI(title="Zelo Voice Service", version="0.1")
    application.state.settings = configuration
    application.state.stt = stt or build_stt(configuration)
    application.state.tts = tts or build_tts(configuration)

    @application.get("/health")
    async def health(request: Request) -> Response:
        if not _authorized(request, configuration):
            return _unauthorized()
        return JSONResponse(
            content={
                "status": "healthy",
                "stt": configuration.stt_provider,
                "tts": configuration.tts_provider,
            },
            headers={"Cache-Control": "no-store"},
        )

    @application.post("/transcribe")
    async def transcribe(request: Request) -> Response:
        if not _authorized(request, configuration):
            return _unauthorized()
        media_type = request.headers.get("content-type", "").lower()
        if not media_type.startswith("audio/"):
            return JSONResponse(
                status_code=415,
                content={"detail": "audio_content_type_required"},
            )

        audio, rejected = await _bounded_body(
            request,
            configuration.max_audio_bytes,
        )
        if rejected is not None:
            if rejected.status_code == 413:
                rejected = JSONResponse(
                    status_code=413,
                    content={"detail": "audio_too_large"},
                )
            return rejected
        if not audio:
            return JSONResponse(
                status_code=400,
                content={"detail": "audio_required"},
            )

        try:
            result = application.state.stt.transcribe(audio, media_type)
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={"detail": "stt_unavailable"},
            )
        if not result.text:
            return JSONResponse(
                status_code=422,
                content={"detail": "speech_not_detected"},
            )
        return JSONResponse(
            content={"text": result.text, "language": result.language},
            headers={"Cache-Control": "no-store"},
        )

    @application.post("/synthesize")
    async def synthesize(request: Request) -> Response:
        if not _authorized(request, configuration):
            return _unauthorized()

        max_json_bytes = configuration.max_text_chars * 4 + 1024
        raw, rejected = await _bounded_body(request, max_json_bytes)
        if rejected is not None:
            return JSONResponse(
                status_code=rejected.status_code,
                content={"detail": "voice_request_too_large"},
            )
        if not raw:
            return JSONResponse(
                status_code=400,
                content={"detail": "voice_request_required"},
            )
        try:
            body = SynthesisRequest.model_validate_json(raw)
        except ValidationError:
            return JSONResponse(
                status_code=422,
                content={"detail": "invalid_synthesis_request"},
            )
        if len(body.text) > configuration.max_text_chars:
            return JSONResponse(
                status_code=413,
                content={"detail": "text_too_large"},
            )
        try:
            audio = application.state.tts.synthesize(body.text, body.language)
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={"detail": "tts_unavailable"},
            )
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    return application


app = create_voice_app()
