"""Narrow Core adapter over the qualified one-shot document renderer."""

import asyncio
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api_contracts import ApiEndpointRegistry
from app.browser_contracts import BrowserExecutionSpec
from app.browser_operator import canonical, load_receipt, private_directory
from app.config import Settings
from app.developer_contracts import WorkerSelectionSnapshot
from app.schemas import BrowserPayload, BrowserRunResult
from qualification.admission import AdmissionController, AdmissionDenied
from qualification.broker import Policy
from qualification.dns import QualificationError
from qualification.renderer import DocumentRenderer
from qualification.renderer_contracts import RendererContract
from qualification.runtime_contracts import validate_digest


class BrowserRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BrowserValidation:
    runtime: object
    identity: str
    receipt_digest: str | None


class BrowserRuntime:
    """Constructor/factory injection is trusted assembly, never request configuration."""

    def __init__(
        self,
        endpoints: ApiEndpointRegistry,
        contract: RendererContract,
        bundle: Path,
        runtime: Path,
        admission_directory: Path,
        receipt_identity: str,
        *,
        renderer_factory: Callable[..., DocumentRenderer] = DocumentRenderer,
        receipt_path: Path | None = None,
    ) -> None:
        validate_digest(receipt_identity)
        self.endpoints, self.contract, self.bundle = endpoints, contract, bundle
        self.runtime = private_directory(runtime)
        self.admission_directory = private_directory(admission_directory)
        self.admission = AdmissionController(self.admission_directory)
        self.bindings = tuple(endpoints.bind(item.endpoint_id) for item in endpoints.list())
        self.receipt_identity, self.receipt_path = receipt_identity, receipt_path
        self.renderer_factory = renderer_factory
        self._owners: set[DocumentRenderer] = set()
        self._closing = False
        self._validate()
        self.identity = self._identity()

    def _identity(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "receipt": self.receipt_identity,
                    "bundle": str(self.bundle),
                    "runtime": str(self.runtime),
                    "admission": str(self.admission_directory),
                    "contract": self.contract.digest,
                    "registry": self.endpoints.version,
                    "adapter": "zelo-browser-core-v1",
                }
            )
        ).hexdigest()

    def _validate(self) -> None:
        private_directory(self.runtime)
        private_directory(self.admission_directory)
        if not self.bundle.is_absolute() or self.bundle.resolve() != self.bundle:
            raise ValueError("browser_bundle_not_canonical")
        if self.receipt_path is None:
            self.contract.verify_bundle(self.bundle)
        self.contract.verify_broker(self.endpoints, self.bindings, Policy())
        if self.receipt_path is not None:
            contract, identity = load_receipt(self.receipt_path, self.bundle, self.endpoints)
            if contract != self.contract or identity != self.receipt_identity:
                raise ValueError("browser_receipt_changed")

    def _receipt_digest(self) -> str | None:
        if self.receipt_path is None:
            return None
        path = self.receipt_path
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("receipt_path")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or not 0 < info.st_size <= 2097152
            ):
                raise ValueError("receipt_private_file")
            with os.fdopen(fd, "rb", closefd=False) as source:
                raw = source.read(2097153)
            if len(raw) > 2097152:
                raise ValueError("receipt_limit")
            return hashlib.sha256(raw).hexdigest()
        finally:
            os.close(fd)

    def prepare_validation(self) -> BrowserValidation:
        """Full file validation before a Core writer transaction, never cached."""
        try:
            before = self._receipt_digest()
            self._validate()
            if before != self._receipt_digest() or self._closing:
                raise ValueError("browser_configuration_changed")
            return BrowserValidation(self, self._identity(), before)
        except (ValueError, OSError):
            raise BrowserRuntimeError("browser_execution_spec_invalid") from None

    def bind_validated(
        self,
        validation: BrowserValidation | None,
        worker_selection: WorkerSelectionSnapshot,
        endpoint_id: str,
        path: str,
        timeout_seconds: float,
    ) -> BrowserExecutionSpec:
        try:
            if (
                validation is None
                or validation.runtime is not self
                or validation.identity != self._identity()
                or validation.receipt_digest != self._receipt_digest()
                or self._closing
            ):
                raise ValueError("browser_validation_stale")
            private_directory(self.runtime)
            private_directory(self.admission_directory)
            return self._bound_spec(worker_selection, endpoint_id, path, timeout_seconds)
        except (ValueError, OSError):
            raise BrowserRuntimeError("browser_execution_spec_invalid") from None

    def bind(
        self,
        worker_selection: WorkerSelectionSnapshot,
        endpoint_id: str,
        path: str,
        timeout_seconds: float,
    ) -> BrowserExecutionSpec:
        return self.bind_validated(
            self.prepare_validation(), worker_selection, endpoint_id, path, timeout_seconds
        )

    def _bound_spec(
        self,
        worker_selection: WorkerSelectionSnapshot,
        endpoint_id: str,
        path: str,
        timeout_seconds: float,
    ) -> BrowserExecutionSpec:
        try:
            BrowserPayload(endpoint_id=endpoint_id, path=path)
            if self._closing or self.identity != self._identity():
                raise ValueError("browser_closed")
            return BrowserExecutionSpec(
                endpoint=self.endpoints.bind(endpoint_id),
                worker_selection=worker_selection,
                policy_version=self.contract.digest,
                operator_identity=self.identity,
                path=path,
                timeout_seconds=timeout_seconds,
            )
        except (ValueError, OSError):
            raise BrowserRuntimeError("browser_execution_spec_invalid") from None

    def matches(self, spec: BrowserExecutionSpec) -> bool:
        try:
            return spec == self.bind(
                spec.worker_selection,
                spec.endpoint.endpoint.endpoint_id,
                spec.path,
                spec.timeout_seconds,
            )
        except (BrowserRuntimeError, ValueError):
            return False

    def matches_validated(
        self, validation: BrowserValidation | None, spec: BrowserExecutionSpec
    ) -> bool:
        try:
            return spec == self.bind_validated(
                validation,
                spec.worker_selection,
                spec.endpoint.endpoint.endpoint_id,
                spec.path,
                spec.timeout_seconds,
            )
        except (BrowserRuntimeError, ValueError):
            return False

    @staticmethod
    def request_matches(
        spec: BrowserExecutionSpec, payload: dict[str, Any], timeout_seconds: float
    ) -> bool:
        try:
            request = BrowserPayload.model_validate(payload)
            return (
                request.endpoint_id == spec.endpoint.endpoint.endpoint_id
                and request.path == spec.path
                and timeout_seconds == spec.timeout_seconds
            )
        except ValueError:
            return False

    def _prune_owners(self) -> None:
        # Only qualified cleanup state permits dropping a retained owner. This
        # never changes admission files or reconciles an uncertain lifecycle.
        for renderer in tuple(self._owners):
            if renderer.owner.cleanup_confirmed:
                self._owners.discard(renderer)

    async def run(
        self,
        *,
        spec: BrowserExecutionSpec,
        path: str,
        timeout_seconds: float,
        emit: Callable[[str, dict[str, Any]], None],
        deadline: float | None = None,
        authorize_action: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        self._prune_owners()
        loop = asyncio.get_running_loop()
        limit = loop.time() + timeout_seconds
        deadline = min(deadline, limit) if deadline is not None else limit
        renderer = None
        try:
            async with asyncio.timeout_at(deadline):
                if not await asyncio.to_thread(self.matches, spec) or not self.request_matches(
                    spec,
                    {"endpoint_id": spec.endpoint.endpoint.endpoint_id, "path": path},
                    timeout_seconds,
                ):
                    raise BrowserRuntimeError("browser_execution_spec_stale")
                if authorize_action is not None and not authorize_action():
                    raise BrowserRuntimeError("browser_agent_execution_invalid")
                # Trusted synchronous factories create no external resources.
                renderer = await asyncio.to_thread(
                    self.renderer_factory,
                    self.admission,
                    self.contract,
                    self.bundle,
                    self.runtime,
                    self.endpoints,
                    self.bindings,
                )
                if self._closing:
                    raise BrowserRuntimeError("browser_closed")
                self._prune_owners()
                self._owners.add(renderer)
                emit(
                    "browser_execution_started", {"endpoint_id": spec.endpoint.endpoint.endpoint_id}
                )
                if authorize_action is not None and not authorize_action():
                    raise BrowserRuntimeError("browser_agent_execution_invalid")
                render_options = (
                    {"authorize_action": authorize_action} if authorize_action is not None else {}
                )
                evidence = await renderer.render(
                    spec.endpoint.endpoint.endpoint_id,
                    path,
                    deadline=deadline,
                    **render_options,
                )
            if not renderer.owner.cleanup_confirmed:
                raise BrowserRuntimeError("browser_cleanup_unproven")
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if authorize_action is not None and not authorize_action():
                raise BrowserRuntimeError("browser_agent_execution_invalid")
            if (
                evidence.source_origin != spec.endpoint.endpoint.origin
                or evidence.source_path != path
                or evidence.contract_digest != spec.policy_version
                or evidence.broker_policy_digest != self.contract.execution.broker_digest
                or evidence.instruction_trust != "none"
            ):
                raise BrowserRuntimeError("browser_evidence_identity_mismatch")
            result = BrowserRunResult(
                endpoint_id=spec.endpoint.endpoint.endpoint_id,
                **asdict(evidence),
                completed_at=datetime.now(UTC),
            )
            emit(
                "browser_execution_completed",
                {
                    "endpoint_id": result.endpoint_id,
                    "nodes": result.nodes,
                    "truncated": result.truncated,
                },
            )
            return result.model_dump(mode="json")
        except asyncio.CancelledError:
            raise
        except BrowserRuntimeError:
            raise
        except AdmissionDenied:
            raise BrowserRuntimeError("browser_admission_denied") from None
        except TimeoutError:
            raise BrowserRuntimeError("browser_timeout") from None
        except QualificationError:
            raise BrowserRuntimeError("browser_cleanup_or_execution_failed") from None
        except Exception:
            raise BrowserRuntimeError("browser_execution_failed") from None
        finally:
            # ResourceOwner retains incomplete lifecycle tasks and the marker. Never
            # reinterpret owner_failed/cleanup_pending as successful cleanup.
            if renderer is not None and not renderer.owner.cleanup_required:
                self._owners.discard(renderer)
            self._prune_owners()

    async def aclose(self) -> None:
        self._closing = True
        for renderer in tuple(self._owners):
            try:
                await renderer.aclose()
            except Exception:
                pass
            if not renderer.owner.cleanup_required:
                self._owners.discard(renderer)
        if self._owners:
            raise BrowserRuntimeError("browser_cleanup_unproven")


def configured_browser_runtime(settings: Settings) -> BrowserRuntime | None:
    if not settings.browser_worker_enabled:
        return None
    try:
        paths = (
            settings.browser_worker_receipt,
            settings.browser_worker_bundle,
            settings.browser_worker_runtime,
            settings.browser_worker_admission,
        )
        if any(path is None for path in paths) or not settings.browser_worker_endpoints:
            raise ValueError("missing_browser_configuration")
        registry = ApiEndpointRegistry.from_entries(settings.browser_worker_endpoints)
        contract, identity = load_receipt(paths[0], paths[1], registry)
        return BrowserRuntime(
            registry, contract, paths[1], paths[2], paths[3], identity, receipt_path=paths[0]
        )
    except (ValueError, OSError):
        raise ValueError("browser_operator_configuration_invalid") from None
