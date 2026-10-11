"""Print the exact trusted startup opt-in needed for YouTube Research.

No database, model, runtime worker or network use. Informational only.
"""

import json

from app.agent_factory import default_agent_registry
from app.youtube_research_specialist import (
    youtube_research_spec_digest,
    youtube_research_specialist_spec,
)


def main() -> int:
    baseline = default_agent_registry()
    spec = youtube_research_specialist_spec()
    print(
        json.dumps(
            {
                "schema": "zelo-youtube-research-optin-preview-v1",
                "baseline_registry_version": baseline.version,
                "agent_id": spec.agent_id,
                "agent_spec_sha256": youtube_research_spec_digest(),
                "startup_env_var": "ZELO_YOUTUBE_RESEARCH_APPROVED_SPEC_SHA256",
                "already_enabled": False,
                "creates_agent_instance": False,
                "tool_grants": list(spec.allowed_tools),
                "task_kinds": list(spec.allowed_task_kinds),
                "publishing_enabled": False,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
