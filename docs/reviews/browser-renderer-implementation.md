# Bounded renderer implementation — offline qualification slice

Implemented in the dedicated `feat/v0.15-browser-renderer-integration` worktree,
starting at `32deae2` on verified main `e16d370`. No production registration,
application behavior, migration, UI, dependency, service, version or release change.
No commit, push, PR or merge was performed by this implementation owner.

## Delivered path and authority

`qualification/renderer.py` exposes one-shot `DocumentRenderer.render(endpoint_id,
path)`. It validates the existing strict `ApiPayload` path and binds approved
registry entries. One existing ResourceOwner lease covers PageSession creation,
DNS/HTTP fetch, Docker creation, anonymous pipe execution and cleanup. An admission
adapter records the **complete RendererContract digest** in the existing durable
marker, rather than only the nested ExecutionPolicy digest. No admission semantics
or production API Worker contract were changed.

The frozen contract binds the ExecutionPolicy, complete captured ArtifactManifest,
required `bootstrap`, `browser` and `loader` executable roles, exact generated
SeccompProfile bytes/digest, controller source digest, renderer policy identity,
approved registry/bindings and the prepared image receipt. Bundle scanning occurs
before admission and again before execution/start. The broker retains its original
policy digest; its absolute session deadline is clipped to the global job deadline
before fetch. No remote headers, cookies or URLs reach the renderer.

`PreparedImage(image_identity, manifest_digest)` is explicitly **trusted operator
input**. It records the offline preparer's assertion that the actual immutable image
contains those artifacts. It does not establish that assertion itself, sign it, or
turn scanner equality into image-content proof. The parent-owned preparation harness
must verify the stopped image filesystem before issuing this receipt. The runtime
then checks the exact local `sha256:` image identity and Linux/amd64 configuration;
there is no per-job build, pull or image deletion. Mutable source paths and a trusted
same-UID operator are not claimed to be frozen or defeated by hashing.

## Process and cleanup boundary

`renderer_process.py` uses `/usr/bin/docker` against the fixed local Unix socket,
without a shell or inherited environment. Its sole environment entry points Docker
at the job's private empty configuration directory, avoiding the user's Docker
contexts/credential configuration. CLI stdout/JSON is bounded to 128 KiB and stderr
to 16 KiB, with three-second CLI deadlines and retained kill/drain/reap. The renderer
attach pipe has separately bounded stdout and stderr. Constructor spawn/factory
seams are trusted tests/operator code, never endpoint/path authority.

Fixed container arguments enforce nonroot 65534, cap-drop ALL, NNP, exact seccomp,
network none, read-only root, private IPC/cgroup/PID/UTS, 512 MiB memory and equal
memory-swap (zero additional swap), 128 PIDs, CPU 50000/100000, 32 MiB tmpfs and
16 MiB shared memory. No mounts, ports, external networking, privileged/unconfined
fallback or reusable-image removal is available. Inspect agreement is required
before `start --attach --interactive`; profile bytes are checked in an owned 0700
runtime directory/0600 file. These checks are implemented, but actual kernel
attachment and enforcement remain parent-owned host gates.

Ownership is constructed before external creation. A lease-derived unpredictable
name and label recover containers when create acknowledgement is lost. All in-flight
CLI/creation tasks settle before discovery/removal. Cleanup checks the discovered
container ID, exact name and lease label before removing that ID, then confirms
absence with successful bounded enumeration. Command errors never mean absence.
Lost create acknowledgement with no discovered container remains fenced because
a delayed daemon operation cannot be excluded. Inspection/discovery uncertainty,
failed removal, failed local cleanup or unresolved children similarly cannot return
success. Repeated closes share retained cleanup tasks; a grace timeout retains the
lease/lifecycle, while process death leaves the existing durable orphan marker.
No stale-PID kill, automatic marker reconciliation or arbitrary name deletion exists.

`qualification/fixtures/browser_pipe.c` is the fixed PID1 bootstrap derived from
Zelo's reference diagnostic. Parent compiles it offline. It validates nonroot/PID1,
bridges stdin/stdout to Chromium fd3/4, executes one fixed browser/options set with
native sandbox enabled and a fresh tmpfs profile, and waits/reaps its main child.
Explicit ALRM/TERM/INT handlers exit PID1; the 25-second watchdog and namespace exit
terminate remaining namespace children. No compiler or browser was run here.

## Controller and evidence bounds

`renderer_protocol.py` uses sequential IDs and one outstanding request. It bounds
individual messages to 512 KiB, combined request/response traffic to 2 MiB, events
to 32, commands to 24, DOM depth to 16, nodes to 4096, extracted UTF-8 text to 64 KiB,
and all protocol operations to the job's absolute deadline. It rejects duplicate
JSON keys, malformed/nonfinite JSON (including float overflow), unsolicited or
mismatched responses, errors, unknown events and framing/EOF/byte floods. No reader
creates an unbounded queue. The single requested attachment event is validated
against its target/page/about:blank/context and matching response session; duplicate
or unsolicited attachment events fail. The one expected `Page.documentOpened`
notification is accepted during the fixed document transfer or its following
DOM/frame query, for the exact
session and about:blank root frame without a parent; duplicates are denied.

A fresh browser context and about:blank page are created. Downloads are denied;
scripts are disabled before HTML transfer; service workers are bypassed; network
URLs are blocked and all fetch requests intercepted without a continuation API.
Network-none remains the security boundary. The controller prepends fixed
`default-src 'none'` CSP (without unsupported meta sandbox), transfers broker HTML
as JSON data or escapes plain text, and uses only fixed DOM methods. It checks the
root URL/frame identity before and after extraction. It never navigates to a source
URL or evaluates page-selected JavaScript.

Output is **DOM text**, not a claim of CSS-visible text. Extraction preserves text
node order, excludes script/style/head/meta/template instructions, bounds traversal,
and marks omitted/depth-limited child trees and unsupported document/shadow subtrees
as truncated. Excess supplied depth/nodes are denied. Output stays untrusted and
carries origin/path, raw source digest, broker policy digest and complete contract
digest. Host code never executes returned HTML.

The event policy intentionally fails closed on unexpected frame/navigation/window,
network/resource or download notifications. Resource-heavy pages may be denied,
not rendered best-effort. Live Chromium event compatibility and script/native
sandbox behavior are **not proven by the doubles**. The parent must qualify those
against the actual image; do not widen this into a generic CDP forwarding API.

## Actual offline checks

Interpreter: `/home/zelo/zelo/core/.venv/bin/python`.

Focused command:

```text
-m pytest -q tests/test_browser_renderer.py \
  tests/test_renderer_protocol_host_regressions.py \
  tests/test_browser_broker.py tests/test_browser_dns.py \
  tests/test_browser_runtime_owner.py tests/test_browser_artifacts.py \
  tests/test_browser_seccomp.py
```

The parent-owned protocol regression file was read and executed without modifying
it. New implementation tests use asyncio.run, controlled stream/daemon doubles,
HTTPX MockTransport (no network), and two real fixed Python subprocess fixtures for
output-overrun and cancellation/creation ownership. Coverage includes fixed command
shape, DOM bounds/order/incomplete trees, hostile strings remaining document data,
response/event/byte/EOF failures, pre-admission path/binding validation, contract and
profile/image/config drift, lease before fetch/create, lost acknowledgements,
failure/cancel/deadline stages, stderr flood, cleanup races/grace/failures and no
result before confirmed cleanup. Existing owner tests cover independent-process
contention and crash-fenced admission.

Final focused result: **400 passed, 1 skipped, 3 existing warnings in 2.17s**.
This includes 76 new implementation tests and the parent's 15 offline protocol
regressions. The existing artifact fixture skipped because this environment forbids
a controlled Unix socket bind. No skipped host/browser gate is counted as proof.
Ruff lint and format checks passed for all five owned Python files. `git diff
--check` passed, and separate `git diff --no-index --check /dev/null <path>` checks
covered all seven new owned files (normal new-file exit status 1 with empty check
output was accepted). No tracked existing file was modified by this owner.

## Parent handoff and remaining gates

Provide one canonical trusted persistent 0700 admission/runtime directory, exact
approved registry/bindings, the original broker Policy, captured bundle and verified
prepared-image receipt. Build the C bootstrap into `/browser-pipe`, put browser
assets in `/opt/browser`, libraries in `/opt/libs`, and the loader at its fixed
image location. Image environment must exactly match `renderer_process.ENV`;
entrypoint must be `['/browser-pipe']`, with no command/volumes/healthcheck or ports.
The image must already exist locally on the fixed daemon. Execution never prepares
it. Source roles are exported as `renderer_contracts.ROLES` for preparation.

Parent owns `scripts/qualify_browser_renderer.py`, its harness tests, actual image
attachment verification, controlled host/browser probes, optional diagnostics,
independent review, full pytest and exact-head CI. Those files were left unchanged
by this owner. No host/browser/image/network evidence is claimed by this report.
No full pytest, Docker invocation, browser launch, image build/pull, external request,
secret access, other-worktree write or release operation was performed here.
The qualification modules are not an activated Browser Worker/task/capability.

## Parent follow-through (supersedes pending host claims above)

Parent image export verification and ten real container/controller/lifecycle cases
passed. HTTP/DNS are controlled fixtures; the browser, pipe, lease, Docker daemon
and cleanup are actual. Parent added exact real-event ordering regressions and
truthful Chromium long-text preview handling. New focused gate: 117 passed.

The image verifier never extracts untrusted archives. It checks payload bytes and
modes plus exact inventory, allowing only named Docker metadata placeholders and
the exact mount-table metadata alias. No arbitrary symlink or code is accepted.

This remains qualification-only. PreparedImage construction is a trusted operator
API, not untrusted receipt parsing or signature checking. Raw DOM text may be
truncated/inert or the document may be denied; no general interaction authority is
provided. See the integration document for reproducible opt-in invocation and
parent evidence. Full exact-head CI and independent review are still pending.
