"""One bounded self-improvement preparation cycle with no mutation authority."""

import asyncio
from typing import Literal

from pydantic import Field, model_validator

from app.self_improvement import (
    Contract,
    ImprovementProposal,
    RepositoryInspector,
    SelfImprovementError,
)
from app.self_improvement_planner import SelfImprovementPlanner


class SelfImprovementPreparation(Contract):
    goal: str = Field(min_length=1, max_length=1024)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    inspection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tracked_path_count: int = Field(ge=0, le=4096)
    inspected_content_file_count: int = Field(ge=0, le=128)
    content_truncated: bool
    proposal: ImprovementProposal
    planner_alias: Literal["local_general"]
    planner_model: str = Field(min_length=1, max_length=256)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_binding(self):
        if (
            self.proposal.goal != self.goal
            or self.proposal.base_sha != self.base_sha
            or self.proposal.inspection_fingerprint != self.inspection_fingerprint
        ):
            raise ValueError("self_improvement_preparation_binding_invalid")
        return self


class SelfImprovementRuntime:
    """Inspect and plan exactly one improvement; never modify the repository."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        planner: SelfImprovementPlanner,
    ) -> None:
        self.inspector = inspector
        self.planner = planner

    async def prepare(self, goal: str) -> SelfImprovementPreparation:
        try:
            inspection = await asyncio.to_thread(self.inspector.inspect)
            plan = await self.planner.plan(inspection, goal=goal)
            proposal = plan.proposal
            if (
                proposal.goal != goal
                or proposal.base_sha != inspection.base_sha
                or proposal.inspection_fingerprint != inspection.fingerprint
            ):
                raise SelfImprovementError("self_improvement_preparation_binding_invalid")
            return SelfImprovementPreparation(
                goal=goal,
                base_sha=inspection.base_sha,
                inspection_fingerprint=inspection.fingerprint,
                tracked_path_count=len(inspection.tracked_paths),
                inspected_content_file_count=len(inspection.files),
                content_truncated=inspection.content_truncated,
                proposal=proposal,
                planner_alias=plan.alias,
                planner_model=plan.actual_model,
                prompt_tokens=plan.prompt_tokens,
                completion_tokens=plan.completion_tokens,
                total_tokens=plan.total_tokens,
            )
        except SelfImprovementError:
            raise
        except Exception:
            raise SelfImprovementError("self_improvement_runtime_error") from None
