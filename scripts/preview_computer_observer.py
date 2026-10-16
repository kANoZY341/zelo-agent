"""Print the exact Computer Specialist startup selection; never activate it.

Run from the repository with: python -m scripts.preview_computer_observer
This does not construct Settings, open a database, read .env or start a service.
"""

import json

from app.computer_observer import computer_observer_registry, computer_observer_spec


def main() -> None:
    print(
        json.dumps(
            {
                "schema_version": "zelo-computer-observer-startup-preview-v1",
                "setting": "ZELO_COMPUTER_OBSERVER_APPROVED_REGISTRY_SHA256",
                "approved_registry_sha256": computer_observer_registry().version,
                "candidate_spec": computer_observer_spec().model_dump(mode="json"),
                "runtime_started": False,
                "instance_created": False,
                "computer_permissions_granted": False,
                "space_activation_created": False,
                "requires_operator_review": True,
            },
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
