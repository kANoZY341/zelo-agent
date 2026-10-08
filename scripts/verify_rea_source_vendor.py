"""Report REA's pinned, locally vendored source identity without executing it."""

import json
from dataclasses import asdict

from qualification.rea_source_vendor import verify_vendored_rea_source


def main() -> None:
    receipt = verify_vendored_rea_source()
    print(json.dumps({"status": "source_verified_inert", **asdict(receipt)}, sort_keys=True))


if __name__ == "__main__":
    main()
