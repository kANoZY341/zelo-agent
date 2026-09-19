"""Trusted, local-only construction of an approval-bound promotion commit."""

import hashlib
import json
import os
from pathlib import Path

from app.developer import DeveloperRuntime, identifier, no_symlinks
from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError, environment, run_process
from app.developer_sandbox import git_argv
from app.promotion_contracts import CandidateEntry, PromotionCommitResult, PromotionRequest
from app.promotion_review import PromotionReviewer, read_candidate_bytes
from app.promotion_validation import validate_promotion_candidate

ZERO_SHA = "0" * 40


class PromotionCommitter:
    """Create a deterministic local commit without credentials, remotes, or hooks."""

    def __init__(self, runtime: DeveloperRuntime, reviewer: PromotionReviewer) -> None:
        self.runtime = runtime
        self.reviewer = reviewer

    def root(self) -> Path:
        workspace = no_symlinks(self.runtime.settings.workspace_root)
        root = no_symlinks(workspace / "promotion")
        if workspace == self.runtime.source_repository or root == self.runtime.source_repository:
            raise DeveloperError("promotion_workspace_escape")
        return root

    def path(self, attempt_id: str) -> Path:
        root = self.root()
        path = no_symlinks(root / identifier(attempt_id) / "repo")
        if not path.is_relative_to(root) or path == self.runtime.source_repository:
            raise DeveloperError("promotion_workspace_escape")
        return path

    async def _git(
        self,
        repo: Path,
        request: DeveloperRequest,
        *args: str,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> bytes:
        result = await run_process(
            git_argv(*args),
            cwd=repo,
            timeout=request.limits.git_seconds,
            stdout_limit=request.limits.stdout_bytes,
            stderr_limit=request.limits.stderr_bytes,
            stdin=stdin,
            env=env,
        )
        if result.exit_code:
            raise DeveloperError("promotion_git_operation_failed")
        return result.stdout

    async def _prepare_repo(
        self,
        task_id: str,
        attempt_id: str,
        request: DeveloperRequest,
    ) -> Path:
        root = self.root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = no_symlinks(root / identifier(attempt_id))
        if parent.exists():
            raise DeveloperError("promotion_workspace_exists")
        parent.mkdir(mode=0o700)
        owner = parent / "owner.json"
        owner.write_text(
            json.dumps({"task": identifier(task_id), "execution": attempt_id, "active": True})
        )
        os.chmod(owner, 0o600)
        repo = parent / "repo"
        repo.mkdir(mode=0o700)
        await self._git(repo, request, "init", "--quiet")
        await self._git(
            repo,
            request,
            "-c",
            "protocol.file.allow=always",
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            str(self.runtime.source_repository),
            request.base_sha,
        )
        if await self._git(repo, request, "remote"):
            raise DeveloperError("promotion_remote_forbidden")
        object_type = (
            (await self._git(repo, request, "cat-file", "-t", request.base_sha)).decode().strip()
        )
        if object_type != "commit":
            raise DeveloperError("promotion_base_invalid")
        await self._git(repo, request, "read-tree", request.base_sha)
        return repo

    async def _hash_candidate(
        self,
        promotion_repo: Path,
        developer_repo: Path,
        request: DeveloperRequest,
        entry: CandidateEntry,
    ) -> str:
        content = read_candidate_bytes(developer_repo, entry)
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry.sha256 or len(content) != entry.size:
            raise DeveloperError("promotion_candidate_changed")
        blob = (
            (
                await self._git(
                    promotion_repo,
                    request,
                    "hash-object",
                    "-w",
                    "--stdin",
                    stdin=content,
                )
            )
            .decode()
            .strip()
        )
        if len(blob) != 40 or any(character not in "0123456789abcdef" for character in blob):
            raise DeveloperError("promotion_blob_invalid")
        return blob

    async def _build_tree(
        self,
        promotion_repo: Path,
        developer_repo: Path,
        request: DeveloperRequest,
        promotion: PromotionRequest,
    ) -> str:
        expected: dict[str, tuple[str, str | None, str]] = {}
        for entry in promotion.review.candidate_manifest:
            if entry.operation == "delete":
                await self._git(
                    promotion_repo,
                    request,
                    "update-index",
                    "--force-remove",
                    "--",
                    entry.path,
                )
                expected[entry.path] = ("D", None, entry.mode)
                continue
            blob = await self._hash_candidate(promotion_repo, developer_repo, request, entry)
            await self._git(
                promotion_repo,
                request,
                "update-index",
                "--add",
                "--cacheinfo",
                entry.mode,
                blob,
                entry.path,
            )
            expected[entry.path] = ("A" if entry.operation == "add" else "M", blob, entry.mode)

        tree = (await self._git(promotion_repo, request, "write-tree")).decode().strip()
        if len(tree) != 40:
            raise DeveloperError("promotion_tree_invalid")

        raw = await self._git(
            promotion_repo,
            request,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            "-z",
            request.base_sha,
            tree,
        )
        tokens = [item for item in raw.split(b"\0") if item]
        if len(tokens) % 2:
            raise DeveloperError("promotion_tree_mismatch")
        observed: dict[str, str] = {}
        for index in range(0, len(tokens), 2):
            try:
                status = tokens[index].decode("ascii", "strict")
                path = tokens[index + 1].decode("utf-8", "strict")
            except UnicodeError:
                raise DeveloperError("promotion_tree_mismatch") from None
            if status not in {"A", "M", "D"} or path in observed:
                raise DeveloperError("promotion_tree_mismatch")
            observed[path] = status
        if observed != {path: values[0] for path, values in expected.items()}:
            raise DeveloperError("promotion_tree_mismatch")

        for path, (operation, blob, mode) in expected.items():
            record = await self._git(
                promotion_repo,
                request,
                "ls-tree",
                "-z",
                tree if operation != "D" else request.base_sha,
                "--",
                path,
            )
            if operation == "D":
                if not record:
                    raise DeveloperError("promotion_tree_mismatch")
                if await self._git(promotion_repo, request, "ls-tree", "-z", tree, "--", path):
                    raise DeveloperError("promotion_tree_mismatch")
                continue
            if not record or b"\t" not in record:
                raise DeveloperError("promotion_tree_mismatch")
            metadata, raw_path = record.rstrip(b"\0").split(b"\t", 1)
            fields = metadata.split()
            if (
                len(fields) != 3
                or fields[0].decode() != mode
                or fields[2].decode() != blob
                or raw_path.decode("utf-8", "strict") != path
            ):
                raise DeveloperError("promotion_tree_mismatch")
        return tree

    @staticmethod
    def _commit_environment(promotion: PromotionRequest) -> dict[str, str]:
        env = environment()
        stamp = promotion.commit_timestamp.isoformat()
        env.update(
            {
                "GIT_AUTHOR_NAME": promotion.commit_author_name,
                "GIT_AUTHOR_EMAIL": promotion.commit_author_email,
                "GIT_COMMITTER_NAME": promotion.commit_author_name,
                "GIT_COMMITTER_EMAIL": promotion.commit_author_email,
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_DATE": stamp,
            }
        )
        return env

    async def _create_commit(
        self,
        repo: Path,
        request: DeveloperRequest,
        promotion: PromotionRequest,
        tree: str,
    ) -> str:
        message = promotion.commit_title
        if promotion.commit_message:
            message += "\n\n" + promotion.commit_message
        message += "\n"
        commit = (
            (
                await self._git(
                    repo,
                    request,
                    "commit-tree",
                    tree,
                    "-p",
                    request.base_sha,
                    stdin=message.encode("utf-8"),
                    env=self._commit_environment(promotion),
                )
            )
            .decode()
            .strip()
        )
        if len(commit) != 40:
            raise DeveloperError("promotion_commit_invalid")
        ref = "refs/heads/" + promotion.branch
        await self._git(repo, request, "check-ref-format", ref)
        await self._git(repo, request, "update-ref", ref, commit, ZERO_SHA)
        if (await self._git(repo, request, "rev-parse", ref)).decode().strip() != commit:
            raise DeveloperError("promotion_commit_invalid")
        parent = (await self._git(repo, request, "rev-parse", commit + "^")).decode().strip()
        if parent != request.base_sha:
            raise DeveloperError("promotion_commit_invalid")
        observed_tree = (
            (await self._git(repo, request, "rev-parse", commit + "^{tree}")).decode().strip()
        )
        if observed_tree != tree:
            raise DeveloperError("promotion_commit_invalid")
        if await self._git(repo, request, "remote"):
            raise DeveloperError("promotion_remote_forbidden")
        return commit

    async def create(
        self,
        task_id: str,
        attempt_id: str,
        promotion: PromotionRequest,
        developer_request: DeveloperRequest,
    ) -> PromotionCommitResult:
        developer_repo = self.runtime.path(promotion.review.developer_attempt_id)
        before = await self.runtime.primary_snapshot(developer_request)
        await self.reviewer.reverify(promotion.review, developer_request)

        validation = await validate_promotion_candidate(
            self.runtime, developer_repo, developer_request
        )
        if len(validation) != 4 or any(result.exit_code for result in validation):
            raise DeveloperError("promotion_validation_failed")
        await self.reviewer.reverify(promotion.review, developer_request)
        if await self.runtime.primary_snapshot(developer_request) != before:
            raise DeveloperError("primary_repository_changed")

        repo = None
        try:
            repo = await self._prepare_repo(task_id, attempt_id, developer_request)
            tree = await self._build_tree(repo, developer_repo, developer_request, promotion)
            commit = await self._create_commit(repo, developer_request, promotion, tree)
            if await self.runtime.primary_snapshot(developer_request) != before:
                raise DeveloperError("primary_repository_changed")
            return PromotionCommitResult(
                base_sha=developer_request.base_sha,
                tree_sha=tree,
                commit_sha=commit,
                branch=promotion.branch,
                review_fingerprint=promotion.review.review_fingerprint,
                validation=validation,
            )
        finally:
            if repo is not None:
                owner = repo.parent / "owner.json"
                try:
                    data = json.loads(owner.read_text())
                    if (
                        data.get("task") == task_id
                        and data.get("execution") == attempt_id
                        and data.get("active") is True
                    ):
                        data["active"] = False
                        owner.write_text(json.dumps(data))
                        os.chmod(owner, 0o600)
                except (OSError, json.JSONDecodeError):
                    pass
