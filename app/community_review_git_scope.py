"""Derive Open Code Review line anchors from trusted, immutable Git revisions.

This is a read-only candidate/evidence adapter, NOT a Worker, reviewer grant,
merge approval or background CI action. Never use model-supplied diff metadata.
"""

import hashlib
import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.community_review import bind_ocr_review

SHA = re.compile(r"[a-f0-9]{40}\Z")
SAFE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/ -]{0,239}\Z")
HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
MAX_FILES = 64
MAX_PATCH = 262144
MAX_TOTAL = 1048576
MAX_CHANGED_LINES = 12000

_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "XDG_CONFIG_HOME": "/nonexistent",
    "XDG_CONFIG_DIRS": "/nonexistent",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
}


class CommunityGitScopeRefusal(ValueError):
    """Git evidence cannot be safely bound to an exact reviewed diff."""


@dataclass(frozen=True, slots=True)
class VerifiedGitReviewScope:
    base_sha: str
    head_sha: str
    diff_sha256: str
    review_patch: bytes
    changed_lines: tuple[tuple[str, frozenset[int]], ...]
    source_data_only: bool = True
    worker_registered: bool = False
    git_write_authority: bool = False
    review_approval_authority: bool = False

    def allowed_lines(self) -> MappingProxyType:
        return MappingProxyType(dict(self.changed_lines))


def _git(repo: Path, *args: str, limit: int, attr_source: str | None = None) -> bytes:
    """Bound output and time; no hooks, textconv, ext-diff, network or shell."""

    child = None
    try:
        fixed_env = _GIT_ENV.copy()
        if attr_source is not None:
            if not SHA.fullmatch(attr_source):
                raise CommunityGitScopeRefusal("ocr_git_attr_identity_invalid")
            fixed_env["GIT_ATTR_SOURCE"] = attr_source
        child = subprocess.Popen(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.attributesFile=/dev/null",
                "-c",
                "diff.orderFile=/dev/null",
                "-c",
                "diff.algorithm=myers",
                "-c",
                "diff.indentHeuristic=false",
                "-c",
                "diff.outputIndicatorNew=+",
                "-c",
                "diff.outputIndicatorOld=-",
                "-c",
                "diff.noprefix=false",
                "-c",
                "diff.mnemonicPrefix=false",
                "-c",
                "diff.relative=false",
                "-c",
                "diff.suppressBlankEmpty=false",
                "-c",
                "core.abbrev=40",
                *args,
            ],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=fixed_env,
        )
        assert child.stdout is not None
        deadline = time.monotonic() + 12
        collected = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise CommunityGitScopeRefusal("ocr_git_evidence_timeout")
                chunk = os.read(child.stdout.fileno(), min(65536, limit + 1 - len(collected)))
                if not chunk:
                    break
                collected.extend(chunk)
                if len(collected) > limit:
                    raise CommunityGitScopeRefusal("ocr_diff_output_limit")
        if child.wait(timeout=max(0.1, deadline - time.monotonic())):
            raise CommunityGitScopeRefusal("ocr_git_evidence_unavailable")
        return bytes(collected)
    except subprocess.TimeoutExpired:
        raise CommunityGitScopeRefusal("ocr_git_evidence_timeout") from None
    except OSError:
        raise CommunityGitScopeRefusal("ocr_git_evidence_unavailable") from None
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            if child.stdout is not None:
                child.stdout.close()


def _path(raw: bytes) -> str:
    try:
        name = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise CommunityGitScopeRefusal("ocr_diff_path_unsupported") from None
    if (
        not SAFE_PATH.fullmatch(name)
        or any(x in ("", ".", "..", ".git") for x in name.split("/"))
        or name.endswith("/")
    ):
        raise CommunityGitScopeRefusal("ocr_diff_path_unsupported")
    return name


def _line_spans(patch: bytes) -> frozenset[int]:
    """Parse LF-delimited Git records; anchor only actual added/new lines.

    Verify every hunk's old/new counters, never trust ranges as changed lines.
    Embedded CRs and source text resembling diff headers cannot create anchors.
    """

    added: set[int] = set()
    saw_hunk = False
    old_left = new_left = current_new_line = 0
    in_hunk = False
    records = patch.split(b"\n")
    if records[-1] == b"":
        records.pop()
    for record in records:
        if record.startswith(b"@@ "):
            if in_hunk and (old_left or new_left):
                raise CommunityGitScopeRefusal("ocr_diff_hunk_count_mismatch")
            match = HUNK.fullmatch(record)
            if match is None:
                raise CommunityGitScopeRefusal("ocr_diff_hunk_invalid")
            old_left = int(match[2] or b"1")
            new_left = int(match[4] or b"1")
            if old_left and not new_left:
                raise CommunityGitScopeRefusal("ocr_unanchorable_deletion")
            current_new_line = int(match[3])
            if (
                new_left > MAX_CHANGED_LINES
                or current_new_line < 0
                or current_new_line + new_left > 1_000_000
            ):
                raise CommunityGitScopeRefusal("ocr_diff_line_limit")
            saw_hunk = in_hunk = True
            continue
        if not in_hunk:
            if record.startswith(b"Binary files ") or record == b"GIT binary patch":
                raise CommunityGitScopeRefusal("ocr_binary_diff_unsupported")
            if record.startswith(b"@@"):
                raise CommunityGitScopeRefusal("ocr_diff_hunk_invalid")
            continue
        if record.startswith(b"+"):
            if new_left == 0:
                raise CommunityGitScopeRefusal("ocr_diff_hunk_count_mismatch")
            added.add(current_new_line)
            current_new_line += 1
            new_left -= 1
        elif record.startswith(b"-"):
            if old_left == 0:
                raise CommunityGitScopeRefusal("ocr_diff_hunk_count_mismatch")
            old_left -= 1
        elif record.startswith(b" "):
            if old_left == 0 or new_left == 0:
                raise CommunityGitScopeRefusal("ocr_diff_hunk_count_mismatch")
            old_left -= 1
            new_left -= 1
            current_new_line += 1
        elif record == b"\\ No newline at end of file":
            continue
        else:
            raise CommunityGitScopeRefusal("ocr_diff_hunk_invalid")
        if len(added) > MAX_CHANGED_LINES:
            raise CommunityGitScopeRefusal("ocr_diff_line_limit")
    if not saw_hunk:
        raise CommunityGitScopeRefusal("ocr_diff_without_text_hunk")
    if old_left or new_left:
        raise CommunityGitScopeRefusal("ocr_diff_hunk_count_mismatch")
    return frozenset(added)


def verified_git_review_scope(
    repo: Path, *, base_sha: str, head_sha: str
) -> VerifiedGitReviewScope:
    """Read exact committed Git source/diff only; never touch live candidate files."""

    if (
        not isinstance(repo, Path)
        or type(base_sha) is not str
        or not SHA.fullmatch(base_sha)
        or type(head_sha) is not str
        or not SHA.fullmatch(head_sha)
        or base_sha == head_sha
    ):
        raise CommunityGitScopeRefusal("ocr_git_review_identity_invalid")
    try:
        root = repo.resolve(strict=True)
        if not root.is_dir() or root != repo.absolute():
            raise CommunityGitScopeRefusal("ocr_git_review_root_invalid")
    except OSError:
        raise CommunityGitScopeRefusal("ocr_git_review_root_invalid") from None
    # GIT_ATTR_SOURCE is not honored by older Git versions. Fail closed.
    version = _git(root, "--version", limit=64)
    match = re.fullmatch(rb"git version (\d+)\.(\d+)\.(\d+)\n", version)
    if match is None or tuple(int(part) for part in match.groups()) < (2, 43, 0):
        raise CommunityGitScopeRefusal("ocr_git_version_unsupported")
    try:
        detected_root = _git(root, "rev-parse", "--show-toplevel", limit=4096).strip()
    except CommunityGitScopeRefusal:
        raise CommunityGitScopeRefusal("ocr_git_review_root_invalid") from None
    if detected_root != os.fsencode(root):
        raise CommunityGitScopeRefusal("ocr_git_review_root_invalid")
    # Git still consults mutable $GIT_DIR/info/attributes even when its
    # working-tree .gitattributes are pinned via GIT_ATTR_SOURCE. Fail closed.
    info_path = _git(root, "rev-parse", "--git-path", "info/attributes", limit=4096)
    if not info_path.endswith(b"\n"):
        raise CommunityGitScopeRefusal("ocr_git_info_attributes_path_invalid")
    info_attributes = Path(os.fsdecode(info_path[:-1]))
    if not info_attributes.is_absolute():
        info_attributes = root / info_attributes
    if info_attributes.exists() or info_attributes.is_symlink():
        raise CommunityGitScopeRefusal("ocr_git_mutable_info_attributes")
    if _git(root, "rev-parse", "HEAD", limit=64).strip() != os.fsencode(head_sha):
        raise CommunityGitScopeRefusal("ocr_git_review_head_stale")
    for commit in (base_sha, head_sha):
        if _git(root, "cat-file", "-t", commit, limit=64).strip() != b"commit":
            raise CommunityGitScopeRefusal("ocr_git_revision_not_commit")
    # A matching tree in an unrelated history is not the submitted change's
    # trusted base. Require an actual ancestry relationship, not Git's
    # permissive ability to diff any two unrelated commit objects.
    try:
        ancestor = _git(root, "merge-base", base_sha, head_sha, limit=64).strip()
    except CommunityGitScopeRefusal:
        raise CommunityGitScopeRefusal("ocr_git_review_base_unrelated") from None
    if ancestor != os.fsencode(base_sha):
        raise CommunityGitScopeRefusal("ocr_git_review_base_unrelated")

    name_list = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--ignore-submodules=none",
        "-O/dev/null",
        "--name-only",
        "-z",
        "--diff-filter=ACMTD",
        base_sha,
        head_sha,
        "--",
        limit=16384,
        attr_source=head_sha,
    )
    if not name_list or not name_list.endswith(b"\0"):
        raise CommunityGitScopeRefusal("ocr_git_review_empty")
    raw_names = name_list[:-1].split(b"\0")
    if len(raw_names) > MAX_FILES:
        raise CommunityGitScopeRefusal("ocr_diff_file_limit")
    paths = tuple(sorted((_path(name) for name in raw_names), key=lambda name: name.encode()))
    if len(set(paths)) != len(paths):
        raise CommunityGitScopeRefusal("ocr_diff_duplicate_file")
    patches: list[bytes] = []
    anchors: list[tuple[str, frozenset[int]]] = []
    total = 0
    for name in paths:
        pathspec = f":(literal){name}"
        # A named diff driver in committed attributes can consult mutable local
        # diff.<name> configuration and change both anchors and patch checksums.
        # Refuse every nondefault diff attribute, including forced text/binary.
        diff_attr = _git(
            root,
            "check-attr",
            "-z",
            f"--source={head_sha}",
            "diff",
            "--",
            name,
            limit=4096,
            attr_source=head_sha,
        )
        if diff_attr != os.fsencode(name) + b"\0diff\0unspecified\0":
            raise CommunityGitScopeRefusal("ocr_custom_diff_attribute_unsupported")
        for revision in (base_sha, head_sha):
            record = _git(root, "ls-tree", "-z", revision, "--", pathspec, limit=4096)
            if not record and revision == base_sha:
                continue  # A new file has no base-tree entry.
            if not record and revision == head_sha:
                raise CommunityGitScopeRefusal("ocr_deleted_file_unsupported")
            prefix = re.match(rb"^(100644|100755) blob [a-f0-9]{40}\t", record)
            if prefix is None or record[prefix.end() :] != os.fsencode(name) + b"\0":
                raise CommunityGitScopeRefusal("ocr_review_file_not_regular")
        patch = _git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--ignore-submodules=none",
            "-O/dev/null",
            "--no-color",
            "--full-index",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--no-relative",
            "--unified=0",
            "--inter-hunk-context=0",
            base_sha,
            head_sha,
            "--",
            pathspec,
            limit=MAX_PATCH,
            attr_source=head_sha,
        )
        change_lines = _line_spans(patch)
        segment = f"=== {name} ({len(patch)} bytes) ===\n".encode() + patch
        total += len(segment)
        if total > MAX_TOTAL:
            raise CommunityGitScopeRefusal("ocr_diff_total_limit")
        patches.append(segment)
        anchors.append((name, change_lines))
    if _git(root, "rev-parse", "HEAD", limit=64).strip() != os.fsencode(head_sha):
        raise CommunityGitScopeRefusal("ocr_git_review_head_changed")
    review_patch = b"zelo-ocr-verified-diff-v1\n" + b"".join(patches)
    return VerifiedGitReviewScope(
        base_sha=base_sha,
        head_sha=head_sha,
        diff_sha256=hashlib.sha256(review_patch).hexdigest(),
        review_patch=review_patch,
        changed_lines=tuple(anchors),
    )


def bind_ocr_review_from_git(raw_review: bytes, repo: Path, *, base_sha: str, head_sha: str):
    """Bind directly to rederived Git scope; caller never supplies line authority."""

    scope = verified_git_review_scope(repo, base_sha=base_sha, head_sha=head_sha)
    report, evidence = bind_ocr_review(
        raw_review,
        approved_changed_lines=scope.allowed_lines(),
        base_sha=scope.base_sha,
        head_sha=scope.head_sha,
        diff_sha256=scope.diff_sha256,
    )
    return report, evidence, scope
