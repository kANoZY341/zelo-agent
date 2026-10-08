"""Explicit-only, fixed-fixture REA MCP static analysis proof. No live Core binding."""

import argparse
import json
import os
import stat

from qualification.rea_static_mcp import (
    NATIVE_FIXTURE_ROOT,
    ReaStaticRefusal,
    _reject_symlink_ancestors,
    analyze_static_fixture,
)

PACKAGE_JSON = b'{"name":"zelo-rea-fixture","version":"1.0.0","main":"index.js"}\n'
INDEX_JS = b"function double(v) { return v * 2; }\nmodule.exports = { double };\n"


def _child_directory(parent_fd: int, name: str) -> int:
    """Create/open only one component relative to an already-verified parent FD."""

    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )


def _exact_fixture_file(parent_fd: int, name: str, expected: bytes) -> None:
    """Never overwrite an existing file, follow a symlink, or trust path races."""

    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
    except FileExistsError:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ReaStaticRefusal("rea_fixture_not_regular")
            if before.st_size != len(expected):
                raise ReaStaticRefusal("rea_fixture_modified")
            seen = b""
            while len(seen) < len(expected):
                data = os.read(descriptor, len(expected) - len(seen))
                if not data:
                    raise ReaStaticRefusal("rea_fixture_modified")
                seen += data
            after = os.fstat(descriptor)
            if seen != expected or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_ino,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise ReaStaticRefusal("rea_fixture_modified")
        finally:
            os.close(descriptor)
        return

    try:
        remaining = expected
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise ReaStaticRefusal("rea_fixture_write_failed")
            remaining = remaining[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_fixture() -> None:
    """Prepare exactly one known benign fixture without traversing symlink parents."""

    if not NATIVE_FIXTURE_ROOT.is_dir():
        raise ReaStaticRefusal("rea_fixture_workspace_unavailable")
    _reject_symlink_ancestors(NATIVE_FIXTURE_ROOT)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(NATIVE_FIXTURE_ROOT, flags)
        try:
            fixtures_fd = _child_directory(root_fd, "fixtures")
            try:
                app_fd = _child_directory(fixtures_fd, "tiny-app")
                try:
                    _exact_fixture_file(app_fd, "package.json", PACKAGE_JSON)
                    _exact_fixture_file(app_fd, "index.js", INDEX_JS)
                    if set(os.listdir(app_fd)) != {"package.json", "index.js"}:
                        raise ReaStaticRefusal("rea_fixture_extra_files")
                finally:
                    os.close(app_fd)
            finally:
                os.close(fixtures_fd)
        finally:
            os.close(root_fd)
    except OSError:
        raise ReaStaticRefusal("rea_fixture_path_unavailable") from None


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
