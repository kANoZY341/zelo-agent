"""Separately approved exact-main-SHA release tag creation."""

from typing import Any
from urllib.parse import quote

from app.config import Settings
from app.developer_process import DeveloperError
from app.promotion_contracts import ReleaseRequest, ReleaseRunResult
from app.promotion_github import GhApiTransport, GitHubTransport, publisher_identity

HEX = set("0123456789abcdef")


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeveloperError("promotion_release_response_invalid")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise DeveloperError("promotion_release_response_invalid")
    return value


def _sha(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in HEX for character in value)
    ):
        raise DeveloperError("promotion_release_response_invalid")
    return value


class PromotionReleaseRuntime:
    """Create or discover only the exact approved tag on the exact main SHA."""

    def __init__(
        self,
        settings: Settings,
        transport: GitHubTransport | None = None,
    ) -> None:
        if settings.promotion_repository is None:
            raise DeveloperError("promotion_repository_unconfigured")
        self.settings = settings
        self.repository = settings.promotion_repository
        self.transport = transport or GhApiTransport(settings)

    def endpoint(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            raise DeveloperError("promotion_release_request_invalid")
        return f"/repos/{self.repository}{suffix}"

    async def _require_main(self, request: ReleaseRequest) -> None:
        response = _dict(
            await self.transport.request(
                "GET",
                self.endpoint("/git/ref/heads/main"),
            )
        )
        if _sha(_dict(response.get("object")).get("sha")) != request.main_sha:
            raise DeveloperError("promotion_release_main_changed")

    async def _existing(self, request: ReleaseRequest) -> ReleaseRunResult | None:
        encoded = quote(f"tags/{request.tag}", safe="/")
        matches = _list(
            await self.transport.request(
                "GET",
                self.endpoint(f"/git/matching-refs/{encoded}"),
            )
        )
        exact_ref = f"refs/tags/{request.tag}"
        exact = [
            _dict(item)
            for item in matches
            if isinstance(item, dict) and item.get("ref") == exact_ref
        ]
        if not exact:
            return None
        if len(exact) != 1:
            raise DeveloperError("promotion_release_tag_conflict")
        obj = _dict(exact[0].get("object"))
        object_type = obj.get("type")
        object_sha = _sha(obj.get("sha"))
        if object_type == "commit":
            if object_sha != request.main_sha:
                raise DeveloperError("promotion_release_tag_conflict")
            return ReleaseRunResult(
                repository=self.repository,
                branch="main",
                main_sha=request.main_sha,
                tag=request.tag,
                tag_object_sha=object_sha,
                annotated=False,
                reused=True,
            )
        if object_type != "tag":
            raise DeveloperError("promotion_release_tag_conflict")
        tag = _dict(
            await self.transport.request(
                "GET",
                self.endpoint(f"/git/tags/{object_sha}"),
            )
        )
        target = _dict(tag.get("object"))
        if (
            tag.get("tag") != request.tag
            or target.get("type") != "commit"
            or _sha(target.get("sha")) != request.main_sha
        ):
            raise DeveloperError("promotion_release_tag_conflict")
        return ReleaseRunResult(
            repository=self.repository,
            branch="main",
            main_sha=request.main_sha,
            tag=request.tag,
            tag_object_sha=object_sha,
            annotated=True,
            reused=True,
        )

    async def verify_ready(self, request: ReleaseRequest) -> ReleaseRunResult | None:
        if (
            request.repository != self.repository
            or request.publisher != publisher_identity(self.settings)
        ):
            raise DeveloperError("promotion_release_publisher_changed")
        await self._require_main(request)
        return await self._existing(request)

    async def run(self, request: ReleaseRequest) -> ReleaseRunResult:
        existing = await self.verify_ready(request)
        if existing is not None:
            return existing

        tagger = {
            "name": request.tagger_name,
            "email": request.tagger_email,
            "date": request.tagger_date.isoformat(),
        }
        created = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/tags"),
                {
                    "tag": request.tag,
                    "message": request.tag_message,
                    "object": request.main_sha,
                    "type": "commit",
                    "tagger": tagger,
                },
            )
        )
        tag_object_sha = _sha(created.get("sha"))
        created_target = _dict(created.get("object"))
        if (
            created.get("tag") != request.tag
            or created_target.get("type") != "commit"
            or _sha(created_target.get("sha")) != request.main_sha
        ):
            raise DeveloperError("promotion_release_tag_object_mismatch")

        await self._require_main(request)
        response = _dict(
            await self.transport.request(
                "POST",
                self.endpoint("/git/refs"),
                {
                    "ref": f"refs/tags/{request.tag}",
                    "sha": tag_object_sha,
                },
            )
        )
        if (
            response.get("ref") != f"refs/tags/{request.tag}"
            or _sha(_dict(response.get("object")).get("sha")) != tag_object_sha
        ):
            raise DeveloperError("promotion_release_ref_mismatch")

        verified = await self._existing(request)
        if (
            verified is None
            or not verified.annotated
            or verified.tag_object_sha != tag_object_sha
        ):
            raise DeveloperError("promotion_release_verification_failed")
        return verified.model_copy(update={"reused": False})
