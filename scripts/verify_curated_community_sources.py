"""Read-only integrity qualification of pinned, never-executed community sources."""

import json

from app.community_sources import CommunitySourceRefusal, verify_community_sources


def main() -> int:
    try:
        entries = verify_community_sources()
    except CommunitySourceRefusal:
        print(json.dumps({"status": "refused", "reason": "community_source_integrity_unavailable"}))
        return 2
    print(
        json.dumps(
            {
                "status": "curated_sources_verified_inert",
                "sources": [
                    {
                        "name": x.name,
                        "repository": x.repository,
                        "commit": x.commit,
                        "license": x.license,
                        "files": x.file_count,
                        "execution_enabled": False,
                        "tool_grants": [],
                    }
                    for x in entries
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
