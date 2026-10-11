"""Inspect the exact operator opt-in for a real, manually bootstrapped Media team.

No database access, no Agent creation, no Worker, no network or external account.
The source manifest/blueprint verifier reads only retained pinned local files.
"""

import json

from app.youtube_company_team import (
    TEAM_LEVELS,
    TEAM_ROLE_IDS,
    _require_pinned_harness_role_identity,
    youtube_company_registry_digest,
)


def main() -> int:
    fingerprint = _require_pinned_harness_role_identity()
    print(
        json.dumps(
            {
                "schema_version": "zelo-youtube-company-optin-preview-v1",
                "template": "youtube_content",
                "startup_env": "ZELO_YOUTUBE_COMPANY_APPROVED_REGISTRY_SHA256",
                "required_exact_registry_sha256": youtube_company_registry_digest(),
                "source_blueprint_sha256": fingerprint,
                "roles": [
                    {"agent_id": role, "level": level}
                    for role, level in zip(TEAM_ROLE_IDS, TEAM_LEVELS, strict=True)
                ],
                "creates_agent_instances": False,
                "creates_project_or_campaign": False,
                "adds_tool_or_space_grants": False,
                "publishing_enabled": False,
                "manual_operator_org_creation_required": True,
                "company_uses_distinct_standalone_pilot": False,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
