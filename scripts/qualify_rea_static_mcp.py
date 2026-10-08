"""Explicit-only, fixed-fixture REA MCP static analysis proof. No live Core binding."""

import argparse
import json

from qualification.rea_static_mcp import (
    NATIVE_FIXTURE_ROOT,
    ReaStaticRefusal,
    analyze_static_fixture,
)

PACKAGE_JSON = b'{"name":"zelo-rea-fixture","version":"1.0.0","main":"index.js"}\n'
INDEX_JS = b"function double(v) { return v * 2; }\nmodule.exports = { double };\n"


def _prepare_fixture() -> None:
    root = NATIVE_FIXTURE_ROOT / "fixtures" / "tiny-app"
    if root.is_symlink():
        raise ReaStaticRefusal("rea_fixture_symlink")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name, content in (("package.json", PACKAGE_JSON), ("index.js", INDEX_JS)):
        target = root / name
        if target.is_symlink():
            raise ReaStaticRefusal("rea_fixture_symlink")
        if target.exists():
            if not target.is_file() or target.read_bytes() != content:
                raise ReaStaticRefusal("rea_fixture_modified")
            continue
        with target.open("xb") as stream:
            stream.write(content)
        target.chmod(0o600)
    if set(child.name for child in root.iterdir()) != {"package.json", "index.js"}:
        raise ReaStaticRefusal("rea_fixture_extra_files")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fixed REA static MCP qualification")
    parser.add_argument(
        "--allow-native-rea-probe",
        action="store_true",
        help="Explicitly opt in to a resource-bounded local Docker qualification",
    )
    args = parser.parse_args()
    if not args.allow_native_rea_probe:
        print(json.dumps({"status": "refused", "reason": "explicit_opt_in_required"}))
        return 2
    try:
        _prepare_fixture()
        result = analyze_static_fixture()
    except ReaStaticRefusal as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1
    print(json.dumps({"status": "qualified_fixture", "receipt": result.snapshot()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
