"""Trusted GitHub publication of an exact approval-bound local promotion commit."""

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from app.config import ROOT, Settings
from app.developer import DeveloperRuntime
from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError, environment, run_process
from app.promotion_contracts import (
    PromotionCommitResult,
    PromotionPublisherIdentity,
    PromotionPublishResult,
    PromotionRequest,
)
from app.promotion_review import PromotionReviewer, read_candidate_bytes

GATEWAY_VERSION = "github-promotion-v1"
HEX = set("0123456789abcdef")
MAX_GH_BYTES = 256 * 1024 * 1024


def publisher_identity(settings: Settings) -> PromotionPublisherIdentity:
    """Bind the exact trusted GitHub CLI and credential location without reading the token."""
    if settings.promotion_repository is None:
        raise DeveloperError("promotion_repository_unconfigured")
    executable = settings.promotion_gh_executable
    config_dir = settings.promotion_gh_config_dir
    executable_fd = None
    try:
        if executable.is_symlink() or not executable.is_absolute():
            raise DeveloperError("promotion_github_cli_untrusted")
        resolved = executable.resolve(strict=True)
        if resolved != executable:
            raise DeveloperError("promotion_github_cli_untrusted")
        executable_fd = os.open(
            resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        before = os.fstat(executable_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_mode & 0o022
            or not 0 < before.st_size <= MAX_GH_BYTES
        ):
            raise DeveloperError("promotion_github_cli_untrusted")
        digest = hashlib.sha256()
        total = 0
        while block := os.read(executable_fd, 65536):
            total += len(block)
            if total > MAX_GH_BYTES:
                raise DeveloperError("promotion_github_cli_untrusted")
            digest.update(block)
        after = os.fstat(executable_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise DeveloperError("promotion_github_cli_changed")

        if config_dir.is_symlink() or not config_dir.is_absolute():
            raise DeveloperError("promotion_github_auth_unavailable")
        resolved_config = config_dir.resolve(strict=True)
        if resolved_config != config_dir or not resolved_config.is_dir():
            raise DeveloperError("promotion_github_auth_unavailable")
        config_stat = resolved_config.stat()
        if config_stat.st_uid not in {0, os.getuid()} or config_stat.st_mode & 0o022:
            raise DeveloperError("promotion_github_auth_untrusted")

        return PromotionPublisherIdentity(
            gateway_version=GATEWAY_VERSION,
            repository=settings.promotion_repository,
            host="github.com",
            gh_executable=str(resolved),
            gh_executable_sha256=digest.hexdigest(),
            gh_config_dir=str(resolved_config),
            timeout_seconds=settings.promotion_github_timeout_seconds,
        )
    except OSError:
        raise DeveloperError("promotion_github_cli_unavailable") from None
    finally:
        if executable_fd is not None:
            os.close(executable_fd)


class GitHubTransport(Protocol):
    async def request(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> Any: ...


class GhApiTransport:
    """Use operator-authenticated gh without ever extracting or passing its token."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _environment(self) -> dict[str, str]:
        config = self.settings.promotion_gh_config_dir
        if config.is_symlink() or not config.is_dir():
            raise DeveloperError("promotion_github_auth_unavailable")
        env = environment()
        env.update(
            {
                "GH_CONFIG_DIR": str(config),
                "GH_HOST": "github.com",
                "GH_PROMPT_DISABLED": "1",
                "GH_PAGER": "cat",
                "NO_COLOR": "1",
            }
        )
        return env

    async def request(self, method: str, endpoint: str, body: dict[str, Any] | None = None) -> Any:
        if method not in {"GET", "POST"} or not endpoint.startswith("/repos/"):
            raise DeveloperError("promotion_github_request_invalid")
        executable = self.settings.promotion_gh_executable
        if executable.is_symlink() or not executable.is_file() or not executable.is_absolute():
            raise DeveloperError("promotion_github_cli_unavailable")
        argv = [
            str(executable),
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            endpoint,
        ]
        stdin = None
        if body is not None:
            if method != "POST":
                raise DeveloperError("promotion_github_request_invalid")
            argv.extend(["--input", "-"])
            try:
                stdin = json.dumps(
                    body,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("ascii")
            except (TypeError, ValueError):
                raise DeveloperError("promotion_github_request_invalid") from None
        result = await run_process(
            argv,
            cwd=ROOT,
            timeout=self.settings.promotion_github_timeout_seconds,
            stdout_limit=131072,
            stderr_limit=8192,
            stdin=stdin,
            env=self._environment(),
        )
        if result.exit_code:
            raise DeveloperError("promotion_github_api_failed")
        try:
            return json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError):
            raise DeveloperError("promotion_github_response_invalid") from None


def _sha(value: Any, code: str = "promotion_github_response_invalid") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in HEX for character in value)
    ):
        raise DeveloperError(code)
    return value


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeveloperError("promotion_github_response_invalid")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise DeveloperError("promotion_github_response_invalid")
    return value


class GitHubPromotionPublisher:
    """Publish only the exact reviewed tree/commit to one operator-configured repository."""

    def __init__(
        self,
        settings: Settings,
        runtime: DeveloperRuntime,
        reviewer: PromotionReviewer,
        transport: GitHubTransport | None = None,
    ) -> None:
        if settings.promotion_repository is None:
            raise DeveloperError("promotion_repository_unconfigured")
        self.settings = settings
        self.runtime = runtime
        self.reviewer = reviewer
        self.repository = settings.promotion_repository
        self.transport = transport or GhApiTransport(settings)

    def endpoint(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            raise DeveloperError("promotion_github_request_invalid")
        return f"/repos/{self.repository}{suffix}"

    async def _base_sha(self, promotion: PromotionRequest) -> str:
        branch = quote(promotion.target_base_branch, safe="")
        response = _dict(
            await self.transport.request("GET", self.endpoint(f"/git/ref/heads/{branch}"))
        )
        return _sha(_dict(response.get("object")).get("sha"))

    async def _require_base(self, promotion: PromotionRequest) -> None:
        if await self._base_sha(promotion) != promotion.review.base_sha:
            raise DeveloperError("promotion_remote_base_changed")

    async def _base_tree(self, promotion: PromotionRequest) -> str:
        response = _dict(
            await self.transport.request(
                "GET", self.endpoint(f"/git/commits/{promotion.review.base_sha}")
            )
        )
        if _sha(response.get("sha")) != promotion.review.base_sha:
            raise DeveloperError("promotion_remote_base_changed")
        return _sha(_dict(response.get("tree")).get("sha"))

    async def _blob(self, content: bytes) -> str:
        response = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/blobs"),
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            )
        )
        return _sha(response.get("sha"))

    async def _tree(
        self,
        promotion: PromotionRequest,
        local: PromotionCommitResult,
        developer_repo: Path,
        developer_request: DeveloperRequest,
    ) -> None:
        base_tree = await self._base_tree(promotion)
        entries: list[dict[str, Any]] = []
        for entry in promotion.review.candidate_manifest:
            if entry.operation == "delete":
                entries.append(
                    {
                        "path": entry.path,
                        "mode": entry.mode,
                        "type": "blob",
                        "sha": None,
                    }
                )
                continue
            content = read_candidate_bytes(developer_repo, entry)
            blob = await self._blob(content)
            entries.append(
                {
                    "path": entry.path,
                    "mode": entry.mode,
                    "type": "blob",
                    "sha": blob,
                }
            )
        response = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/trees"),
                {"base_tree": base_tree, "tree": entries},
            )
        )
        if _sha(response.get("sha"), "promotion_remote_tree_mismatch") != local.tree_sha:
            raise DeveloperError("promotion_remote_tree_mismatch")
        await self.reviewer.reverify(promotion.review, developer_request)

    @staticmethod
    def _message(promotion: PromotionRequest) -> str:
        message = promotion.commit_title
        if promotion.commit_message:
            message += "\n\n" + promotion.commit_message
        return message + "\n"

    async def _commit(self, promotion: PromotionRequest, local: PromotionCommitResult) -> None:
        identity = {
            "name": promotion.commit_author_name,
            "email": promotion.commit_author_email,
            "date": promotion.commit_timestamp.isoformat(),
        }
        response = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/commits"),
                {
                    "message": self._message(promotion),
                    "tree": local.tree_sha,
                    "parents": [promotion.review.base_sha],
                    "author": identity,
                    "committer": identity,
                },
            )
        )
        if _sha(response.get("sha"), "promotion_remote_commit_mismatch") != local.commit_sha:
            raise DeveloperError("promotion_remote_commit_mismatch")

    async def _branch(self, promotion: PromotionRequest, local: PromotionCommitResult) -> bool:
        ref = f"refs/heads/{promotion.branch}"
        encoded = quote(f"heads/{promotion.branch}", safe="/")
        matches = _list(
            await self.transport.request("GET", self.endpoint(f"/git/matching-refs/{encoded}"))
        )
        exact = [
            _dict(item) for item in matches if isinstance(item, dict) and item.get("ref") == ref
        ]
        if exact:
            observed = _sha(_dict(exact[0].get("object")).get("sha"))
            if len(exact) != 1 or observed != local.commit_sha:
                raise DeveloperError("promotion_branch_conflict")
            return False
        response = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/refs"),
                {"ref": ref, "sha": local.commit_sha},
            )
        )
        if (
            response.get("ref") != ref
            or _sha(_dict(response.get("object")).get("sha")) != local.commit_sha
        ):
            raise DeveloperError("promotion_branch_create_mismatch")
        return True

    async def _pull_request(
        self,
        promotion: PromotionRequest,
        local: PromotionCommitResult,
        branch_created: bool,
    ) -> PromotionPublishResult:
        owner = self.repository.split("/", 1)[0]
        query = urlencode(
            {
                "state": "all",
                "head": f"{owner}:{promotion.branch}",
                "base": promotion.target_base_branch,
            }
        )
        found = _list(await self.transport.request("GET", self.endpoint(f"/pulls?{query}")))
        if len(found) > 1:
            raise DeveloperError("promotion_pr_ambiguous")
        if found:
            response = _dict(found[0])
            if response.get("state") != "open":
                raise DeveloperError("promotion_pr_closed")
            pr_created = False
        else:
            response = _dict(
                await self.transport.request(
                    "POST",
                    self.endpoint("/pulls"),
                    {
                        "title": promotion.pr_title,
                        "head": promotion.branch,
                        "base": promotion.target_base_branch,
                        "body": promotion.pr_body,
                    },
                )
            )
            pr_created = True
        number = response.get("number")
        url = response.get("html_url")
        head = _dict(response.get("head"))
        base = _dict(response.get("base"))
        if (
            response.get("state") != "open"
            or not isinstance(number, int)
            or number < 1
            or not isinstance(url, str)
            or not url.startswith(f"https://github.com/{self.repository}/pull/")
            or head.get("ref") != promotion.branch
            or _sha(head.get("sha")) != local.commit_sha
            or base.get("ref") != promotion.target_base_branch
            or _sha(base.get("sha")) != promotion.review.base_sha
        ):
            raise DeveloperError("promotion_pr_mismatch")
        return PromotionPublishResult(
            repository=self.repository,
            base_branch=promotion.target_base_branch,
            base_sha=promotion.review.base_sha,
            branch=promotion.branch,
            commit_sha=local.commit_sha,
            pr_number=number,
            pr_url=url,
            pr_head_sha=local.commit_sha,
            pr_base_sha=promotion.review.base_sha,
            branch_created=branch_created,
            pr_created=pr_created,
        )

    async def publish(
        self,
        promotion: PromotionRequest,
        local: PromotionCommitResult,
        developer_request: DeveloperRequest,
    ) -> PromotionPublishResult:
        if promotion.repository != self.repository:
            raise DeveloperError("promotion_repository_changed")
        if promotion.publisher != publisher_identity(self.settings):
            raise DeveloperError("promotion_publisher_changed")
        if (
            local.base_sha != promotion.review.base_sha
            or local.branch != promotion.branch
            or local.review_fingerprint != promotion.review.review_fingerprint
        ):
            raise DeveloperError("promotion_local_commit_mismatch")
        developer_repo = self.runtime.path(promotion.review.developer_attempt_id)
        await self.reviewer.reverify(promotion.review, developer_request)
        await self._require_base(promotion)
        await self._tree(promotion, local, developer_repo, developer_request)
        await self._commit(promotion, local)
        await self.reviewer.reverify(promotion.review, developer_request)
        await self._require_base(promotion)
        branch_created = await self._branch(promotion, local)
        await self._require_base(promotion)
        return await self._pull_request(promotion, local, branch_created)
