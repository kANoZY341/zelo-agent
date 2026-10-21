"""Print a fixed read-only delegation Team for review; never start or grant anything."""

import json

from app.computer_observer import (
    computer_observer_spec,
    computer_read_team_registry,
    space_read_manager_spec,
)


def main() -> None:
    print(
        json.dumps(
            {
                "schema_version": "zelo-space-read-team-preview-v1",
                "setting": "ZELO_COMPUTER_OBSERVER_APPROVED_REGISTRY_SHA256",
                "approved_registry_sha256": computer_read_team_registry().version,
                "candidate_specs": [
                    space_read_manager_spec().model_dump(mode="json"),
                    computer_observer_spec().model_dump(mode="json"),
                ],
                "runtime_started": False,
                "permissions_granted": False,
                "instances_created": False,
                "delegation_created": False,
                "requires_operator_review": True,
            },
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
