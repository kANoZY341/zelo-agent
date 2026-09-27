"""Replaceable local STT/TTS provider interfaces and first implementations."""

from __future__ import annotations

import io
import math
import tempfile
import wave
from array import array
from dataclasses import dataclass
from typing import Protocol

from voice_service.config import VoiceSettings


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None


class STTProvider(Protocol):
    def transcribe(self, audio: bytes, media_type: str | None) -> Transcription: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, language: str) -> bytes: ...


class FasterWhisperSTT:
    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "faster-whisper is not installed; install requirements-voice.txt"
                ) from exc
            self._model = WhisperModel(
                self.settings.stt_model,
                device=self.settings.stt_device,
                compute_type=self.settings.stt_compute_type,
                download_root=str(self.settings.stt_download_root),
            )
        return self._model

    def transcribe(self, audio: bytes, media_type: str | None) -> Transcription:
        suffix = ".webm"
        if media_type:
            if "wav" in media_type:
                suffix = ".wav"
            elif "ogg" in media_type:
                suffix = ".ogg"
            elif "mp4" in media_type or "m4a" in media_type:
                suffix = ".m4a"
        runtime = self.settings.stt_download_root.parents[1] / "tmp"
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=runtime, suffix=suffix) as handle:
            handle.write(audio)
            handle.flush()
            segments, info = self._get_model().transcribe(
                handle.name,
                beam_size=1,
                vad_filter=True,
                task="transcribe",
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            language = getattr(info, "language", None)
        return Transcription(text=text, language=language)


class SherpaSupertonicTTS:
    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._tts = None

    def _get_tts(self):
        if self._tts is not None:
            return self._tts
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "sherpa-onnx is not installed; install requirements-voice.txt"
            ) from exc

        model_dir = self.settings.tts_model_dir
        required = {
            "duration_predictor": "duration_predictor.int8.onnx",
            "text_encoder": "text_encoder.int8.onnx",
            "vector_estimator": "vector_estimator.int8.onnx",
            "vocoder": "vocoder.int8.onnx",
            "tts_json": "tts.json",
            "unicode_indexer": "unicode_indexer.bin",
            "voice_style": "voice.bin",
        }
        missing = [name for name in required.values() if not (model_dir / name).is_file()]
        if missing:
            raise RuntimeError(f"Supertonic model files missing: {', '.join(missing)}")

        supertonic = sherpa_onnx.OfflineTtsSupertonicModelConfig(
            **{key: str(model_dir / filename) for key, filename in required.items()}
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                supertonic=supertonic,
                num_threads=2,
                debug=False,
                provider="cpu",
            )
        )
        self._tts = sherpa_onnx.OfflineTts(config)
        return self._tts

    def synthesize(self, text: str, language: str) -> bytes:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "sherpa-onnx is not installed; install requirements-voice.txt"
            ) from exc

        config = sherpa_onnx.GenerationConfig()
        config.sid = self.settings.tts_speaker_id
        config.num_steps = 8
        config.speed = self.settings.tts_speed
        config.extra["lang"] = language
        audio = self._get_tts().generate(text, config)
        pcm = array("h")
        for sample in audio.samples:
            bounded = max(-1.0, min(1.0, float(sample)))
            pcm.append(int(math.floor(bounded * 32767.0)))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(audio.sample_rate)
            wav.writeframes(pcm.tobytes())
        return output.getvalue()


def build_stt(settings: VoiceSettings) -> STTProvider:
    if settings.stt_provider == "faster-whisper":
        return FasterWhisperSTT(settings)
    raise RuntimeError("Unsupported STT provider")


def build_tts(settings: VoiceSettings) -> TTSProvider:
    if settings.tts_provider == "sherpa-supertonic":
        return SherpaSupertonicTTS(settings)
    raise RuntimeError("Unsupported TTS provider")
