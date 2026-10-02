"""Qualified broker/renderer composition; Core owns registration through its adapter."""

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.api_contracts import ApiEndpointBinding, ApiEndpointRegistry
from app.schemas import ApiPayload
from qualification.admission import AdmissionController
from qualification.broker import PageSession, Policy
from qualification.dns import _consume_exception
from qualification.renderer_contracts import RendererContract
from qualification.renderer_process import DockerHandle
from qualification.resource_owner import ResourceOwner


@dataclass(frozen=True)
class RenderedEvidence:
    text: str
    truncated: bool
    nodes: int
    source_origin: str
    source_path: str
    source_digest: str
    contract_digest: str
    broker_policy_digest: str
    instruction_trust: Literal["none"] = "none"


class _Job:
    def __init__(self, renderer) -> None:
        self.renderer = renderer
        self.session = None
        self._close = None

    async def stop_and_confirm_cleanup(self) -> bool:
        if self._close is None:
            self._close = asyncio.create_task(self._cleanup())
            self._close.add_done_callback(_consume_exception)
        return await asyncio.shield(self._close)

    async def _cleanup(self) -> bool:
        confirmed = True
        try:
            if self.session is not None:
                await self.session.aclose()
        except Exception:
            confirmed = False
        try:
            confirmed = await self.renderer.stop_and_confirm_cleanup() is True and confirmed
        except Exception:
            confirmed = False
        return confirmed


class _ContractAdmission:
    def __init__(self, admission, contract):
        self.admission, self.contract = admission, contract

    def acquire(self, policy_digest):
        if policy_digest != self.contract.execution.digest:
            raise ValueError("admission_contract_mismatch")
        return self.admission.acquire(self.contract.digest)


class DocumentRenderer:
    """One-shot job. Constructors are trusted operator/fixture authority only.

    Session factory must be synchronous and must not create external resources
    before returning its handle. The default PageSession obeys that rule.
    """

    def __init__(
        self,
        admission: AdmissionController,
        contract: RendererContract,
        bundle: Path,
        runtime: Path,
        registry: ApiEndpointRegistry,
        bindings: tuple[ApiEndpointBinding, ...],
        *,
        policy: Policy | None = None,
        session_factory: Callable = PageSession,
        handle_factory: Callable = DockerHandle,
    ):
        policy = policy or Policy()
        contract.verify_broker(registry, bindings, policy)
        contract.verify_bundle(bundle)
        self.contract, self.bundle, self.runtime = contract, bundle, runtime
        self.registry, self.bindings, self.policy = registry, bindings, policy
        self.session_factory, self.handle_factory = session_factory, handle_factory
        self.owner = ResourceOwner(_ContractAdmission(admission, contract), contract.execution)

    async def render(
        self, endpoint_id: str, path: str, *, deadline: float | None = None
    ) -> RenderedEvidence:
        payload = ApiPayload(endpoint_id=endpoint_id, path=path, query={})
        self.contract.verify_broker(self.registry, self.bindings, self.policy)
        self.contract.verify_bundle(self.bundle)
        policy_deadline = (
            asyncio.get_running_loop().time() + self.contract.execution.resources.deadline_seconds
        )

        deadline = min(policy_deadline, deadline) if deadline is not None else policy_deadline

        async def factory(lease, policy):
            # No await or external work: ResourceOwner owns this handle before work begins.
            return _Job(self.handle_factory(lease, self.contract, self.bundle, self.runtime))

        async def work(job):
            async with asyncio.timeout_at(deadline):
                # PageSession's original policy identity is retained; its absolute deadline
                # is clipped before fetch, so DNS/HTTP cannot outlive the global job budget.
                job.session = self.session_factory(
                    self.registry, bindings=self.bindings, policy=self.policy
                )
                job.session._deadline = min(job.session._deadline, deadline)
                evidence = await job.session.fetch(payload.endpoint_id, payload.path)
                result = await job.renderer.render(evidence.text, evidence.media_type, deadline)
                return RenderedEvidence(
                    result.text,
                    result.truncated,
                    result.nodes,
                    evidence.binding.endpoint.origin,
                    payload.path,
                    hashlib.sha256(evidence.body).hexdigest(),
                    self.contract.digest,
                    evidence.policy_digest,
                )

        return await self.owner.run(factory, work)

    async def aclose(self) -> None:
        await self.owner.aclose()
