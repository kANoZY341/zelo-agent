"""Deterministic activation and evaluation gates for trusted AgentSpec templates."""

import hashlib
import json
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_factory import AgentRegistry, AgentSpec
from app.developer_contracts import WorkerDescriptor

AGENT_ACTIVATION_POLICY = "zelo-agent-activation-policy-v1"


class ActivationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentActivationCheck(ActivationContract):
    code: str = Field(min_length=1, max_length=128)
    status: Literal["pass", "fail", "not_applicable"]
    detail: str = Field(min_length=1, max_length=512)


class AgentActivationEvaluation(ActivationContract):
    agent_id: str
    agent_version: int = Field(ge=1)
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    organizational_level: Literal["vp", "director", "manager", "specialist"]
    surface_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["accepted", "rejected"]
    checks: tuple[AgentActivationCheck, ...]
    failure_codes: tuple[str, ...]
    evaluation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentActivationReport(ActivationContract):
    policy_version: Literal["zelo-agent-activation-policy-v1"] = AGENT_ACTIVATION_POLICY
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    surface_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["accepted", "rejected"]
    evaluations: tuple[AgentActivationEvaluation, ...]
    report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class AgentActivationEvaluator:
    """Evaluate active trusted templates against current bounded Core surfaces."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        available_task_kinds: Iterable[str],
        available_tools: Iterable[str],
        worker_descriptors: Iterable[WorkerDescriptor] = (),
    ) -> None:
        self.registry = registry
        self.task_kinds = frozenset(available_task_kinds)
        self.tools = frozenset(available_tools)
        self.workers = tuple(
            sorted(
                (
                    WorkerDescriptor.model_validate(item.model_dump(mode="json"))
                    for item in worker_descriptors
                ),
                key=lambda item: item.worker_id,
            )
        )
        self.surface_digest = _canonical_digest(
            {
                "task_kinds": sorted(self.task_kinds),
                "tools": sorted(self.tools),
                "workers": [item.model_dump(mode="json") for item in self.workers],
            }
        )

    @staticmethod
    def _check(
        code: str,
        passed: bool | None,
        detail: str,
    ) -> AgentActivationCheck:
        status = "not_applicable" if passed is None else ("pass" if passed else "fail")
        return AgentActivationCheck(code=code, status=status, detail=detail)

    def _worker_matches(self, spec: AgentSpec) -> bool | None:
        requirement = spec.worker_requirement
        if requirement is None:
            return None
        required = set(requirement.capabilities)
        return any(
            worker.enabled
            and worker.role == requirement.role
            and required.issubset(worker.capabilities)
            for worker in self.workers
        )

    def evaluate_spec(self, spec: AgentSpec) -> AgentActivationEvaluation:
        registered = self.registry.get_agent(spec.agent_id)
        registry_bound = registered == spec
        missing_tasks = tuple(sorted(set(spec.allowed_task_kinds).difference(self.task_kinds)))
        missing_tools = tuple(sorted(set(spec.allowed_tools).difference(self.tools)))
        executive = spec.organizational_level in {"vp", "director", "manager"}
        specialist = spec.organizational_level == "specialist"
        worker_match = self._worker_matches(spec)

        checks = (
            self._check(
                "registry_binding",
                registry_bound,
                (
                    "template exactly matches the trusted registry"
                    if registry_bound
                    else "template is not an exact trusted registry member"
                ),
            ),
            self._check(
                "task_surface",
                not missing_tasks,
                (
                    "all declared task kinds are available"
                    if not missing_tasks
                    else f"missing task kinds: {','.join(missing_tasks)}"
                ),
            ),
            self._check(
                "tool_surface",
                not missing_tools,
                (
                    "all declared tools are available"
                    if not missing_tools
                    else f"missing tools: {','.join(missing_tools)}"
                ),
            ),
            self._check(
                "worker_requirement",
                worker_match,
                (
                    "no worker requirement declared"
                    if worker_match is None
                    else (
                        "configured worker registry satisfies the declared requirement"
                        if worker_match
                        else "no enabled configured worker satisfies the declared requirement"
                    )
                ),
            ),
            self._check(
                "developer_worker_binding",
                (
                    spec.worker_requirement is not None
                    if "developer_run" in spec.allowed_task_kinds
                    else None
                ),
                (
                    "developer_run is backed by an explicit worker requirement"
                    if "developer_run" in spec.allowed_task_kinds
                    and spec.worker_requirement is not None
                    else (
                        "developer_run requires an explicit worker requirement"
                        if "developer_run" in spec.allowed_task_kinds
                        else "template does not declare developer_run"
                    )
                ),
            ),
            self._check(
                "review_contract",
                (
                    "developer_run" in spec.allowed_task_kinds
                    and "code_review" in spec.capabilities
                    and "review" in spec.procedures
                    if spec.review_required
                    else None
                ),
                (
                    (
                        "review-required template declares developer task, "
                        "review capability and procedure"
                    )
                    if spec.review_required
                    and "developer_run" in spec.allowed_task_kinds
                    and "code_review" in spec.capabilities
                    and "review" in spec.procedures
                    else (
                        "review-required template is missing developer/review contract"
                        if spec.review_required
                        else "template does not require independent review"
                    )
                ),
            ),
            self._check(
                "executive_hiring_authority",
                (
                    spec.allow_subagents
                    and spec.max_subagents > 0
                    and "organization_hire" in spec.permissions
                    and "organization" in spec.procedures
                    if executive
                    else None
                ),
                (
                    "executive template has explicit bounded organization authority"
                    if executive
                    and spec.allow_subagents
                    and spec.max_subagents > 0
                    and "organization_hire" in spec.permissions
                    and "organization" in spec.procedures
                    else (
                        "VP/Director/Manager requires bounded organization_hire authority"
                        if executive
                        else "template is not an executive organizational level"
                    )
                ),
            ),
            self._check(
                "executive_delegation_surface",
                bool(spec.allowed_task_kinds) if executive else None,
                (
                    "executive template declares at least one bounded task kind for child delegation"
                    if executive and spec.allowed_task_kinds
                    else (
                        "VP/Director/Manager hiring authority requires a delegation task surface"
                        if executive
                        else "template is not an executive organizational level"
                    )
                ),
            ),
            self._check(
                "specialist_hiring_boundary",
                "organization_hire" not in spec.permissions if specialist else None,
                (
                    "specialist cannot permanently hire organizational reports"
                    if specialist and "organization_hire" not in spec.permissions
                    else (
                        "specialist may not declare organization_hire"
                        if specialist
                        else "template is not a specialist"
                    )
                ),
            ),
        )
        failures = tuple(sorted(item.code for item in checks if item.status == "fail"))
        payload = {
            "policy_version": AGENT_ACTIVATION_POLICY,
            "registry_version": self.registry.version,
            "surface_digest": self.surface_digest,
            "agent": spec.model_dump(mode="json"),
            "checks": [item.model_dump(mode="json") for item in checks],
            "failure_codes": failures,
        }
        return AgentActivationEvaluation(
            agent_id=spec.agent_id,
            agent_version=spec.version,
            registry_version=self.registry.version,
            organizational_level=spec.organizational_level,
            surface_digest=self.surface_digest,
            status="rejected" if failures else "accepted",
            checks=checks,
            failure_codes=failures,
            evaluation_digest=_canonical_digest(payload),
        )

    def evaluate(self) -> AgentActivationReport:
        evaluations = tuple(
            self.evaluate_spec(spec)
            for spec in self.registry.list_agents()
            if spec.lifecycle == "active"
        )
        status = (
            "rejected" if any(item.status == "rejected" for item in evaluations) else "accepted"
        )
        payload = {
            "policy_version": AGENT_ACTIVATION_POLICY,
            "registry_version": self.registry.version,
            "surface_digest": self.surface_digest,
            "status": status,
            "evaluations": [item.model_dump(mode="json") for item in evaluations],
        }
        return AgentActivationReport(
            registry_version=self.registry.version,
            surface_digest=self.surface_digest,
            status=status,
            evaluations=evaluations,
            report_digest=_canonical_digest(payload),
        )

    def require_spec(self, spec: AgentSpec) -> AgentActivationEvaluation:
        evaluation = self.evaluate_spec(spec)
        if evaluation.status == "rejected":
            first = evaluation.failure_codes[0]
            raise ValueError(f"agent_activation_rejected:{evaluation.agent_id}:{first}")
        return evaluation

    def require(self) -> AgentActivationReport:
        report = self.evaluate()
        for evaluation in report.evaluations:
            if evaluation.status == "rejected":
                first = evaluation.failure_codes[0]
                raise ValueError(f"agent_activation_rejected:{evaluation.agent_id}:{first}")
        return report
