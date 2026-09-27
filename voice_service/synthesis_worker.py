"""Killable persistent TTS worker process for local voice synthesis."""

from __future__ import annotations

from multiprocessing import get_context
from multiprocessing.connection import Connection
from threading import Lock
from uuid import uuid4


class SynthesisWorkerBusy(RuntimeError):
    """Raised when a synthesis request is already active."""


def _worker_main(connection: Connection) -> None:
    from voice_service.providers import get_synthesizer

    synthesizer = get_synthesizer()
    try:
        while True:
            message = connection.recv()
            if message is None:
                return
            request_id, text, language = message
            try:
                audio = synthesizer.synthesize(text, language)
            except ValueError as exc:
                connection.send((request_id, "value_error", str(exc)))
                continue
            except Exception:
                connection.send((request_id, "error", "voice_synthesis_failed"))
                return
            connection.send((request_id, "ok", audio))
    except (EOFError, BrokenPipeError):
        return
    finally:
        connection.close()


class SynthesisWorker:
    """Keep one local TTS model warm while allowing hard cancellation."""

    def __init__(self) -> None:
        self._context = get_context("spawn")
        self._lock = Lock()
        self._process = None
        self._connection: Connection | None = None
        self._active_request_id: str | None = None

    def _reset_locked(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        self._active_request_id = None

        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)

    def _ensure_worker_locked(self) -> Connection:
        process = self._process
        connection = self._connection
        if process is not None and connection is not None and process.is_alive():
            return connection

        self._reset_locked()
        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_worker_main,
            args=(child_connection,),
            daemon=True,
            name="zelo-tts-worker",
        )
        process.start()
        child_connection.close()
        self._process = process
        self._connection = parent_connection
        return parent_connection

    def start(self, text: str, language: str) -> str:
        with self._lock:
            if self._active_request_id is not None:
                raise SynthesisWorkerBusy("voice_synthesis_busy")

            connection = self._ensure_worker_locked()
            request_id = uuid4().hex
            try:
                connection.send((request_id, text, language))
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._reset_locked()
                raise RuntimeError("voice_synthesis_worker_unavailable") from exc
            self._active_request_id = request_id
            return request_id

    def poll(self, request_id: str) -> bytes | None:
        with self._lock:
            if self._active_request_id != request_id:
                raise RuntimeError("voice_synthesis_request_inactive")
            connection = self._connection
            process = self._process
            if connection is None or process is None:
                raise RuntimeError("voice_synthesis_worker_unavailable")

            if not connection.poll():
                if not process.is_alive():
                    self._reset_locked()
                    raise RuntimeError("voice_synthesis_worker_exited")
                return None

            try:
                response_id, status, payload = connection.recv()
            except (EOFError, OSError) as exc:
                self._reset_locked()
                raise RuntimeError("voice_synthesis_worker_unavailable") from exc

            if response_id != request_id:
                self._reset_locked()
                raise RuntimeError("voice_synthesis_worker_protocol_error")

            self._active_request_id = None
            if status == "ok":
                if not isinstance(payload, bytes):
                    self._reset_locked()
                    raise RuntimeError("voice_synthesis_worker_protocol_error")
                return payload
            if status == "value_error":
                raise ValueError(str(payload))
            raise RuntimeError("voice_synthesis_failed")

    def abort(self, request_id: str) -> None:
        with self._lock:
            if self._active_request_id != request_id:
                return
            self._reset_locked()

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            if connection is not None and self._process is not None and self._process.is_alive():
                try:
                    connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                self._process.join(timeout=1.0)
            self._reset_locked()
