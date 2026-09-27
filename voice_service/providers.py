"""Lazy local STT/TTS providers. Speech conversion has no Zelo authority."""

from __future__ import annotations

import io
import os
import pathlib
import tempfile
import threading
import typing


ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def detect_tts_language(text: str) -> str:
    """Choose Arabic when Arabic script dominates; otherwise use English."""
    arabic = 0
    latin = 0
    for character in text:
        codepoint = ord(character)
        if any(start <= codepoint <= end for start, end in ARABIC_RANGES):
            arabic += 1
        elif ("a" <= character.lower() <= "z"):
            latin += 1
    return "ar" if arabic > latin else "en"


def audio_suffix(content_type: str) -> str:
    lowered = content_type.lower().split(";", 1)[0].strip()
    return {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
    }.get(lowered, ".audio")


class Transcriber(typing.Protocol):
    def transcribe(self, audio: bytes, content_type: str) -> dict[str, object]: ...


class Synthesizer(typing.Protocol):
    def synthesize(self, text: str, language: str) -> bytes: ...


class FasterWhisperTranscriber:
    def __init__(self) -> None:
        self.model_name = os.getenv("ZELO_STT_MODEL", "small")
        self.device = os.getenv("ZELO_STT_DEVICE", "cpu")
        self.compute_type = os.getenv("ZELO_STT_COMPUTE_TYPE", "int8")
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(self, audio: bytes, content_type: str) -> dict[str, object]:
        suffix = audio_suffix(content_type)
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            temporary.write(audio)
            temporary.flush()
            with self._lock:
                model = self._load()
                segments, info = model.transcribe(
                    temporary.name,
                    beam_size=5,
                    vad_filter=True,
                )
                text = " ".join(segment.text.strip() for segment in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": float(info.language_probability),
            "model": self.model_name,
        }


class ChatterboxSynthesizer:
    def __init__(self) -> None:
        self.device = os.getenv("ZELO_TTS_DEVICE", "cpu")
        reference = os.getenv("ZELO_TTS_REFERENCE_AUDIO", "").strip()
        self.reference_audio = pathlib.Path(reference).expanduser() if reference else None
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            # Pass a string ("cpu"/"cuda"/"mps"), not torch.device. This also
            # avoids the known CPU-loading edge case in Chatterbox 0.1.7.
            self._model = ChatterboxMultilingualTTS.from_pretrained(
                device=self.device,
            )
        return self._model

    def synthesize(self, text: str, language: str) -> bytes:
        import torchaudio as ta

        chosen_language = (
            detect_tts_language(text) if language == "auto" else language
        )
        if chosen_language not in {"en", "ar"}:
            raise ValueError("unsupported_voice_language")

        kwargs: dict[str, object] = {"language_id": chosen_language}
        if self.reference_audio:
            if not self.reference_audio.is_file():
                raise RuntimeError("configured_voice_reference_missing")
            kwargs["audio_prompt_path"] = str(self.reference_audio)

        with self._lock:
            model = self._load()
            waveform = model.generate(text, **kwargs)
            output = io.BytesIO()
            ta.save(output, waveform, model.sr, format="wav")
        return output.getvalue()


_transcriber: FasterWhisperTranscriber | None = None
_synthesizer: ChatterboxSynthesizer | None = None
_provider_lock = threading.Lock()


def get_transcriber() -> Transcriber:
    global _transcriber
    with _provider_lock:
        if _transcriber is None:
            _transcriber = FasterWhisperTranscriber()
        return _transcriber


def get_synthesizer() -> Synthesizer:
    global _synthesizer
    with _provider_lock:
        if _synthesizer is None:
            provider = os.getenv("ZELO_TTS_PROVIDER", "chatterbox").strip().lower()
            if provider != "chatterbox":
                raise RuntimeError("unsupported_tts_provider")
            _synthesizer = ChatterboxSynthesizer()
        return _synthesizer
