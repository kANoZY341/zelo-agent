"""Operator-requested, inert serialization of an accepted skill proposal."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.auth import OperatorPrincipal
from app.skill_learning import SkillProcedureRevision
from app.skill_learning_store import SkillLearningProposalStore

MAX_ARTIFACT_BYTES = 65_536
_ARTIFACT_ROOT = "skills/procedures"


@dataclass(frozen=True)
class SkillLearningCandidateArtifact:
    proposal_id: str
    review_fingerprint: str
    relative_path: str
    content_sha256: str
    artifact_sha256: str
    content: bytes
    created: bool


def _canonical_bytes(revision: SkillProcedureRevision) -> bytes:
    # The semantic digest is verified by the Pydantic contract; this raw digest
    # binds the exact bytes consumed by the existing promotion manifest.
    revision = SkillProcedureRevision.model_validate(revision.model_dump(mode="json"))
    return json.dumps(
        revision.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _open_dir(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        try:
            os.mkdir(name, mode=0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return os.open(name, flags, dir_fd=parent_fd)


def _write_immutable(root: Path, parts: tuple[str, ...], content: bytes) -> bool:
    if not root.is_absolute():
        raise ValueError("skill_learning_candidate_root_must_be_absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(root, flags)
    directory_fd = root_fd
    opened: list[int] = [root_fd]
    try:
        for part in parts[:-1]:
            directory_fd = _open_dir(directory_fd, part, create=True)
            opened.append(directory_fd)
        filename = parts[-1]
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(filename, file_flags, 0o444, dir_fd=directory_fd)
        except FileExistsError:
            read_fd = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd
            )
            try:
                if not stat.S_ISREG(os.fstat(read_fd).st_mode):
                    raise ValueError("skill_learning_candidate_conflict") from None
                existing = bytearray()
                while chunk := os.read(read_fd, 8192):
                    existing.extend(chunk)
                    if len(existing) > MAX_ARTIFACT_BYTES:
                        raise ValueError("skill_learning_candidate_conflict") from None
            finally:
                os.close(read_fd)
            if bytes(existing) != content:
                raise ValueError("skill_learning_candidate_conflict") from None
            return False
        try:
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("skill_learning_candidate_write_failed")
                view = view[written:]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            os.unlink(filename, dir_fd=directory_fd)
            raise
        else:
            os.close(fd)
        os.fsync(directory_fd)
        return True
    finally:
        for fd in reversed(opened):
            os.close(fd)


class SkillLearningCandidateAdapter:
    """Materialize accepted content under an explicit operator-selected staging root.

    This adapter has no task/API wiring and never updates skill registries or
    invokes Developer, Review, Promotion, CI, or runtime activation.
    """

    def __init__(self, store: SkillLearningProposalStore) -> None:
        self.store = store

    def materialize(
        self,
        proposal_id: str,
        *,
        staging_root: Path,
        principal: OperatorPrincipal,
    ) -> SkillLearningCandidateArtifact:
        stored = self.store.accepted_candidate_basis(proposal_id, principal)
        if stored is None or stored.review is None:
            raise ValueError("skill_learning_candidate_not_accepted_or_stale")
        revision = stored.proposal.proposed_revision
        content = _canonical_bytes(revision)
        content_sha = hashlib.sha256(content).hexdigest()
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("skill_learning_candidate_too_large")
        relative = PurePosixPath(
            _ARTIFACT_ROOT,
            revision.source_skill_id,
            revision.procedure_id,
            f"v{revision.version}-{revision.content_sha256}.json",
        )
        parts = relative.parts
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts)
            or not parts[:2] == ("skills", "procedures")
        ):
            raise ValueError("skill_learning_candidate_path_invalid")
        created = _write_immutable(staging_root, parts, content)
        return SkillLearningCandidateArtifact(
            proposal_id=proposal_id,
            review_fingerprint=stored.review.review_fingerprint,
            relative_path=relative.as_posix(),
            content_sha256=revision.content_sha256,
            artifact_sha256=content_sha,
            content=content,
            created=created,
        )
