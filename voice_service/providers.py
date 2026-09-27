"""Lazy local STT/TTS providers. Speech conversion has no Zelo authority."""

from __future__ import annotations

import sys
import wave
from array import array
from io import BytesIO
from os import getenv
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Protocol

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


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, content_type: str) -> dict[str, object]: ...


class Synthesizer(Protocol):
    def synthesize(self, text: str, language: str) -> bytes: ...


class FasterWhisperTranscriber:
    def __init__(self) -> None:
        self.model_name = getenv("ZELO_STT_MODEL", "small")
        self.device = getenv("ZELO_STT_DEVICE", "cpu")
        self.compute_type = getenv("ZELO_STT_COMPUTE_TYPE", "int8")
        self._model = None
        self._lock = Lock()

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
        with NamedTemporaryFile(suffix=suffix) as temporary:
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


def pcm16_wav(samples, sample_rate: int) -> bytes:
    """Encode normalized mono float samples as a standard PCM16 WAV."""
    frames = array(
        "h",
        (
            max(-32768, min(32767, round(float(sample) * 32767)))
            for sample in samples
        ),
    )
    if sys.byteorder != "little":
        frames.byteswap()

    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames.tobytes())
    return output.getvalue()


class SherpaOnnxVitsSynthesizer:
    """CPU-friendly VITS/Piper fallback behind the same synthesizer contract."""

    def __init__(self) -> None:
        self.device = getenv("ZELO_SHERPA_TTS_DEVICE", "cpu")
        self.num_threads = int(getenv("ZELO_SHERPA_TTS_THREADS", "4"))
        self.speed = float(getenv("ZELO_SHERPA_TTS_SPEED", "1.0"))
        if self.num_threads <= 0:
            raise ValueError("ZELO_SHERPA_TTS_THREADS must be positive")
        if self.speed <= 0:
            raise ValueError("ZELO_SHERPA_TTS_SPEED must be positive")
        self._models: dict[str, object] = {}
        self._lock = Lock()

    @staticmethod
    def _bundle_dir(language: str) -> Path:
        variable = f"ZELO_SHERPA_{language.upper()}_MODEL_DIR"
        configured = getenv(variable, "").strip()
        if not configured:
            raise RuntimeError(f"{variable} is required for sherpa_vits")
        root = Path(configured).expanduser()
        if not root.is_dir():
            raise RuntimeError(f"{variable} does not point to a directory")
        return root

    def _load(self, language: str):
        cached = self._models.get(language)
        if cached is not None:
            return cached

        import sherpa_onnx

        root = self._bundle_dir(language)
        models = sorted(root.glob("*.onnx"))
        if len(models) != 1:
            raise RuntimeError(f"sherpa_vits bundle {root} must contain exactly one .onnx model")
        tokens = root / "tokens.txt"
        data_dir = root / "espeak-ng-data"
        if not tokens.is_file() or not data_dir.is_dir():
            raise RuntimeError(f"sherpa_vits bundle {root} requires tokens.txt and espeak-ng-data/")

        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(models[0]),
                    tokens=str(tokens),
                    data_dir=str(data_dir),
                ),
                provider=self.device,
                num_threads=self.num_threads,
            ),
            max_num_sentences=1,
        )
        if not config.validate():
            raise RuntimeError("invalid_sherpa_vits_configuration")
        model = sherpa_onnx.OfflineTts(config)
        self._models[language] = model
        return model

    def synthesize(self, text: str, language: str) -> bytes:
        chosen_language = (
            detect_tts_language(text) if language == "auto" else language
        )
        if chosen_language not in {"en", "ar"}:
            raise ValueError("unsupported_voice_language")

        import sherpa_onnx

        with self._lock:
            model = self._load(chosen_language)
            generation = sherpa_onnx.GenerationConfig()
            generation.speed = self.speed
            audio = model.generate(text, generation)
        if len(audio.samples) == 0:
            raise RuntimeError("voice_synthesis_empty")
        return pcm16_wav(audio.samples, int(audio.sample_rate))


class ChatterboxSynthesizer:
    def __init__(self) -> None:
        self.device = getenv("ZELO_TTS_DEVICE", "cpu")
        reference = getenv("ZELO_TTS_REFERENCE_AUDIO", "").strip()
        self.reference_audio = Path(reference).expanduser() if reference else None
        self._model = None
        self._lock = Lock()

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
            output = BytesIO()
            ta.save(output, waveform, model.sr, format="wav")
        return output.getvalue()


_transcriber: FasterWhisperTranscriber | None = None
_synthesizer: Synthesizer | None = None
_provider_lock = Lock()


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
            provider = getenv("ZELO_TTS_PROVIDER", "chatterbox").strip().lower()
            if provider == "chatterbox":
                _synthesizer = ChatterboxSynthesizer()
            elif provider == "sherpa_vits":
                _synthesizer = SherpaOnnxVitsSynthesizer()
            else:
                raise RuntimeError("unsupported_tts_provider")
        return _synthesizer
