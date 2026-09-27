"""Loopback-only HTTP boundary for local speech recognition and synthesis."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from voice_service.config import VoiceSettings
from voice_service.providers import STTProvider, TTSProvider, build_stt, build_tts


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")


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
    async def health() -> dict[str, str]:
        return {
            "status": "healthy",
            "stt": configuration.stt_provider,
            "tts": configuration.tts_provider,
        }

    @application.post("/transcribe")
    async def transcribe(request: Request) -> JSONResponse:
        audio = await request.body()
        if not audio:
            return JSONResponse(status_code=400, content={"detail": "audio_required"})
        if len(audio) > configuration.max_audio_bytes:
            return JSONResponse(status_code=413, content={"detail": "audio_too_large"})
        try:
            result = application.state.stt.transcribe(
                audio,
                request.headers.get("content-type"),
            )
        except RuntimeError:
            return JSONResponse(status_code=503, content={"detail": "stt_unavailable"})
        if not result.text:
            return JSONResponse(status_code=422, content={"detail": "speech_not_detected"})
        return JSONResponse(content={"text": result.text, "language": result.language})

    @application.post("/synthesize")
    async def synthesize(body: SynthesisRequest) -> Response:
        if len(body.text) > configuration.max_text_chars:
            return JSONResponse(status_code=413, content={"detail": "text_too_large"})
        try:
            audio = application.state.tts.synthesize(body.text, body.language)
        except RuntimeError:
            return JSONResponse(status_code=503, content={"detail": "tts_unavailable"})
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    return application


app = create_voice_app()
