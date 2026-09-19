"""Canonical, content-addressed review of a retained Developer Agent workspace."""

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from app.developer import DeveloperRuntime, identifier, no_symlinks, review_name
from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError
from app.developer_review import inventory
from app.promotion_contracts import CandidateEntry, IgnoredArtifactSummary, PromotionReview

MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def validate_manifest_path(value: str) -> str:
    """Validate a Git-relative UTF-8 path without normalizing attacker-controlled input."""
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise DeveloperError("promotion_path_invalid") from None
    path = PurePosixPath(value)
    if (
        not value
        or len(encoded) > 4096
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or str(path) != value
    ):
        raise DeveloperError("promotion_path_invalid")
    return value


def canonical_fingerprint(value: dict[str, Any]) -> str:
    """Hash deterministic JSON; non-finite values are rejected rather than normalized."""
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise DeveloperError("promotion_review_invalid") from None
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _fingerprint_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_version": values["review_version"],
        "developer_task_id": values["developer_task_id"],
        "developer_attempt_id": values["developer_attempt_id"],
        "source_repository": values["source_repository"],
        "workspace": values["workspace"],
        "base_sha": values["base_sha"],
        "head_sha": values["head_sha"],
        "worker_identity": values["worker_identity"],
        "validation_identity": values["validation_identity"],
        "validation_profile": values["validation_profile"],
        "validation_version": values["validation_version"],
        "sandbox_version": values["sandbox_version"],
        "validation_passed": values["validation_passed"],
        "candidate_manifest": values["candidate_manifest"],
        "ignored_artifact_summary": values["ignored_artifact_summary"],
    }


def _open_parent(repo: Path, relative: str) -> tuple[int, bytes]:
    parts = relative.split("/")
    root_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = root_fd
    try:
        for part in parts[:-1]:
            child = os.open(
                os.fsencode(part),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            if descriptor != root_fd:
                os.close(descriptor)
            descriptor = child
        if descriptor == root_fd:
            return descriptor, os.fsencode(parts[-1])
        os.close(root_fd)
        return descriptor, os.fsencode(parts[-1])
    except OSError:
        if descriptor != root_fd:
            os.close(descriptor)
        os.close(root_fd)
        raise DeveloperError("promotion_path_invalid") from None


def _stable_stat(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _present_entry(repo: Path, relative: str, operation: str) -> CandidateEntry:
    validate_manifest_path(relative)
    parent_fd, name = _open_parent(repo, relative)
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(before.st_mode):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                opened = os.fstat(descriptor)
                if _stable_stat(opened) != _stable_stat(before):
                    raise DeveloperError("promotion_candidate_changed")
                digest = hashlib.sha256()
                total = 0
                while block := os.read(descriptor, 65536):
                    total += len(block)
                    if total > MAX_MANIFEST_BYTES:
                        raise DeveloperError("promotion_manifest_limit")
                    digest.update(block)
                after_fd = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after_path = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                _stable_stat(after_fd) != _stable_stat(before)
                or _stable_stat(after_path) != _stable_stat(before)
            ):
                raise DeveloperError("promotion_candidate_changed")
            mode = "100755" if before.st_mode & 0o111 else "100644"
            return CandidateEntry(
                path=relative,
                operation=operation,
                object_type="file",
                mode=mode,
                size=before.st_size,
                sha256=digest.hexdigest(),
            )
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=parent_fd)
            target_bytes = os.fsencode(target) if isinstance(target, str) else target
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _stable_stat(after) != _stable_stat(before):
                raise DeveloperError("promotion_candidate_changed")
            return CandidateEntry(
                path=relative,
                operation=operation,
                object_type="symlink",
                mode="120000",
                size=len(target_bytes),
                sha256=hashlib.sha256(target_bytes).hexdigest(),
            )
        raise DeveloperError("promotion_special_file")
    except FileNotFoundError:
        raise DeveloperError("promotion_candidate_changed") from None
    finally:
        os.close(parent_fd)


async def _deleted_entry(
    runtime: DeveloperRuntime,
    repo: Path,
    request: DeveloperRequest,
    relative: str,
) -> CandidateEntry:
    validate_manifest_path(relative)
    raw = await runtime.git(repo, request, "ls-tree", "-z", request.base_sha, "--", relative)
    records = [record for record in raw.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        raise DeveloperError("promotion_base_entry_invalid")
    metadata, raw_name = records[0].split(b"\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or review_name(raw_name) != relative:
        raise DeveloperError("promotion_base_entry_invalid")
    mode = fields[0].decode("ascii", "strict")
    if mode in {"100644", "100755"}:
        object_type = "file"
    elif mode == "120000":
        object_type = "symlink"
    else:
        raise DeveloperError("promotion_special_file")
    return CandidateEntry(
        path=relative,
        operation="delete",
        object_type=object_type,
        mode=mode,
    )


def _parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    tokens = raw.split(b"\0")
    if tokens and not tokens[-1]:
        tokens.pop()
    if len(tokens) % 2:
        raise DeveloperError("promotion_git_status_invalid")
    output: list[tuple[str, str]] = []
    for index in range(0, len(tokens), 2):
        try:
            status_code = tokens[index].decode("ascii", "strict")
        except UnicodeError:
            raise DeveloperError("promotion_git_status_invalid") from None
        if status_code not in {"A", "M", "D", "T"}:
            raise DeveloperError("promotion_git_status_invalid")
        output.append((status_code, review_name(tokens[index + 1])))
    return output


class PromotionReviewer:
    """Build and re-verify immutable review identities without external side effects."""

    def __init__(self, runtime: DeveloperRuntime) -> None:
        self.runtime = runtime

    def _owned_repo(self, task_id: str, attempt_id: str) -> Path:
        identifier(task_id)
        identifier(attempt_id)
        repo = self.runtime.path(attempt_id)
        self.runtime.verify_checkout_path(repo)
        owner = no_symlinks(repo.parent / "owner.json")
        try:
            if not owner.is_file() or owner.stat().st_size > 1024:
                raise DeveloperError("promotion_workspace_unowned")
            data = json.loads(owner.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise DeveloperError("promotion_workspace_unowned") from None
        if set(data) != {"task", "execution", "active"}:
            raise DeveloperError("promotion_workspace_unowned")
        if data["task"] != task_id or data["execution"] != attempt_id:
            raise DeveloperError("promotion_workspace_unowned")
        if data["active"] is not False:
            raise DeveloperError("promotion_workspace_active")
        return repo

    async def _state(
        self,
        task_id: str,
        attempt_id: str,
        request: DeveloperRequest,
    ) -> tuple[str, list[CandidateEntry], list[IgnoredArtifactSummary]]:
        repo = self._owned_repo(task_id, attempt_id)
        head = (await self.runtime.git(repo, request, "rev-parse", "HEAD")).decode().strip()
        if head != request.base_sha:
            raise DeveloperError("promotion_head_changed")
        if await self.runtime.git(repo, request, "remote"):
            raise DeveloperError("promotion_remote_forbidden")

        tracked_raw = await self.runtime.git(repo, request, "ls-files", "-z")
        tracked = [review_name(item) for item in tracked_raw.split(b"\0") if item]
        artifacts, ignored = await inventory(repo, request, tracked)
        ignored_paths = {item["path"] for item in ignored}

        status_raw = await self.runtime.git(
            repo,
            request,
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            request.base_sha,
            "--",
        )
        entries: dict[str, CandidateEntry] = {}
        total_bytes = 0
        for status_code, relative in _parse_name_status(status_raw):
            validate_manifest_path(relative)
            if relative in entries:
                raise DeveloperError("promotion_git_status_invalid")
            if status_code == "D":
                entry = await _deleted_entry(self.runtime, repo, request, relative)
            else:
                operation = "add" if status_code == "A" else "modify"
                entry = _present_entry(repo, relative, operation)
                total_bytes += entry.size or 0
            entries[relative] = entry

        for item in artifacts:
            relative = validate_manifest_path(item["path"])
            if relative in ignored_paths:
                continue
            if relative in entries:
                raise DeveloperError("promotion_git_status_invalid")
            entry = _present_entry(repo, relative, "add")
            total_bytes += entry.size or 0
            entries[relative] = entry

        if total_bytes > MAX_MANIFEST_BYTES:
            raise DeveloperError("promotion_manifest_limit")
        if not entries:
            raise DeveloperError("promotion_empty_candidate")
        if len(entries) > request.limits.changed_files:
            raise DeveloperError("changed_file_limit")

        manifest = sorted(entries.values(), key=lambda item: item.path.encode("utf-8"))
        ignored_summary = sorted(
            [
                IgnoredArtifactSummary(
                    path=validate_manifest_path(item["path"]),
                    object_type=item["type"],
                    size=item["size"],
                )
                for item in ignored
                if item["type"] in {"file", "symlink", "special"}
            ],
            key=lambda item: item.path.encode("utf-8"),
        )
        return head, manifest, ignored_summary

    async def create(
        self,
        task_id: str,
        attempt_id: str,
        request: DeveloperRequest,
        *,
        validation_passed: bool,
    ) -> PromotionReview:
        if not validation_passed:
            raise DeveloperError("promotion_source_not_validated")
        if not self.runtime.compatible(request, request.limits.overall_seconds):
            raise DeveloperError("developer_configuration_changed")
        if self.runtime.validation_identity() != request.validation_identity:
            raise DeveloperError("validation_environment_changed")

        first = await self._state(task_id, attempt_id, request)
        second = await self._state(task_id, attempt_id, request)
        if first != second:
            raise DeveloperError("promotion_candidate_changed")
        head, manifest, ignored = first
        workspace = str(self.runtime.path(attempt_id).relative_to(self.runtime.root()))
        values: dict[str, Any] = {
            "review_version": "promotion-review-v1",
            "developer_task_id": task_id,
            "developer_attempt_id": attempt_id,
            "source_repository": request.source_repository,
            "workspace": workspace,
            "base_sha": request.base_sha,
            "head_sha": head,
            "worker_identity": request.worker.model_dump(mode="json"),
            "validation_identity": request.validation_identity,
            "validation_profile": request.validation_profile,
            "validation_version": request.validation_version,
            "sandbox_version": request.sandbox_version,
            "validation_passed": True,
            "changed_file_count": len(manifest),
            "candidate_manifest": [item.model_dump(mode="json") for item in manifest],
            "ignored_artifact_summary": [item.model_dump(mode="json") for item in ignored],
        }
        values["review_fingerprint"] = canonical_fingerprint(_fingerprint_payload(values))
        return PromotionReview.model_validate(values)

    async def reverify(self, approved: PromotionReview, request: DeveloperRequest) -> None:
        fresh = await self.create(
            approved.developer_task_id,
            approved.developer_attempt_id,
            request,
            validation_passed=True,
        )
        if fresh.review_fingerprint != approved.review_fingerprint or fresh != approved:
            raise DeveloperError("promotion_candidate_changed")
