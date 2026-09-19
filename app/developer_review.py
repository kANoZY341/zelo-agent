"""Bounded artifact inventory executed only in the read-only review sandbox."""

import json
from pathlib import Path

from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError, run_process
from app.developer_sandbox import git_argv, sandbox

# Walk independently of Git ignore rules, including nested ignored directories.
# Never follow symlinks or read generated file contents. Exclude only root Git internals.
INVENTORY = """import json, os, stat, sys
request = json.load(sys.stdin)
tracked = set(request['tracked'])
output = []
visited = 0
def walk(directory, prefix=''):
    global visited
    with os.scandir(directory) as entries:
        for entry in entries:
            visited += 1
            if visited > 50000: raise SystemExit(23)
            name = prefix + entry.name
            name.encode('utf-8', 'strict')
            info = entry.stat(follow_symlinks=False)
            if not prefix and entry.name == '.git': continue
            if entry.name == '.git':
                output.append({'path':name, 'type':'nested_git', 'size':info.st_size})
                if len(output) > request['limit']: raise SystemExit(23)
            if stat.S_ISDIR(info.st_mode):
                walk(entry.path, name + '/')
                continue
            if name in tracked: continue
            kind = ('file' if stat.S_ISREG(info.st_mode) else
                    'symlink' if stat.S_ISLNK(info.st_mode) else 'special')
            output.append({'path':name, 'type':kind, 'size':info.st_size})
            if len(output) > request['limit']: raise SystemExit(23)
walk('.')
print(json.dumps(sorted(output, key=lambda item:item['path']), ensure_ascii=True))
"""


async def inventory(repo: Path, request: DeveloperRequest, tracked: list[str]):
    result = await run_process(
        sandbox(repo, ["/usr/bin/python3", "-I", "-c", INVENTORY], writable=False),
        cwd=repo,
        timeout=request.limits.git_seconds,
        stdout_limit=request.limits.stdout_bytes,
        stderr_limit=request.limits.stderr_bytes,
        stdin=json.dumps({"tracked": tracked, "limit": request.limits.changed_files}).encode(),
    )
    if result.exit_code:
        raise DeveloperError("review_inventory_failed")
    artifacts = json.loads(result.stdout)
    if any(item["type"] == "nested_git" for item in artifacts):
        raise DeveloperError("nested_git_artifact_forbidden")
    if not artifacts:
        return [], []
    paths = b"".join(item["path"].encode() + b"\0" for item in artifacts)
    ignored = await run_process(
        sandbox(
            repo, git_argv("check-ignore", "--stdin", "-z"), writable=False, git_inspection=True
        ),
        cwd=repo,
        timeout=request.limits.git_seconds,
        stdout_limit=request.limits.stdout_bytes,
        stderr_limit=request.limits.stderr_bytes,
        stdin=paths,
    )
    if ignored.exit_code not in (0, 1):
        raise DeveloperError("review_inventory_failed")
    ignored_names = set(ignored.stdout.decode("utf-8", "strict").split("\0"))
    return artifacts, [item for item in artifacts if item["path"] in ignored_names]
