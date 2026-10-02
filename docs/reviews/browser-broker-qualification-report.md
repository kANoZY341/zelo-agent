# Browser fetch/DNS qualification prerequisite report

Scope: qualification-only work developed from `d9d2acc` on
`feat/v0.15-browser-worker-qualification`, based on API Worker merge `8d33d56`.
The initial delegated implementation report is followed by parent host evidence. Browser Worker qualification has **not**
passed and no runtime authority is activated.

## Files

- `qualification/__init__.py`: qualification-only package marker.
- `qualification/broker.py`: explicit immutable endpoint bindings, strict GET/path
  surface, deterministic versioned policy, IP-pinned verified TLS transport, bounded
  raw UTF-8 evidence, fail-fast session admission and owned cleanup.
- `qualification/dns.py`: single-flight fixed-program resolver subprocess,
  environment scrubbing, FQDN/address/output validation, cancellation-safe ownership,
  kill/drain/reap, poison-on-unproven-cleanup and explicit close.
- `tests/test_browser_broker.py`, `tests/test_browser_dns.py`: offline qualification
  fixtures, including actual controlled subprocess death/reap checks.
- `docs/v0.15-browser-worker-qualification.md`: short prerequisite/status update.
- This report.

No changes to `app/`, migrations, requirements, UI, credentials or global services.
No installs, commits, push, PR changes, full-suite runs or renderer attachment.

## Checks

Using the existing interpreter, with no dependency installation:

```text
/home/zelo/zelo/core/.venv/bin/python -m pytest -q tests/test_browser_broker.py tests/test_browser_dns.py
/home/zelo/zelo/core/.venv/bin/python -m ruff check qualification tests/test_browser_broker.py tests/test_browser_dns.py
/home/zelo/zelo/core/.venv/bin/python -m ruff format --check qualification tests/test_browser_broker.py tests/test_browser_dns.py
git diff --check
```

Final results: **66 passed**, three pre-existing warnings, in 0.24 seconds;
Ruff check passed; formatting check passed (five Python files); `git diff --check`
passed. New untracked files were also checked individually with
`git diff --no-index --check /dev/null <file>` without staging. An initial test-harness
attempt used unavailable pytest-asyncio markers; tests now use `asyncio.run`, matching
existing repository tests. No sandbox/thread/subprocess restriction was encountered.
The focused suite emits existing Starlette/AnyIO deprecation warnings and the existing
Pydantic `PerformanceCostEvidence.schema` shadowing warning from shared test imports.

Covered: strict requests and binding mismatches; mixed/private/loopback/multicast/
transition/scoped/noncanonical addresses; numeric IPv4/IPv6 and custom ports;
transport verification/proxy/retry configuration; exact headers and no response-header
forwarding; redirect/status/disposition/MIME/charset/encoding denials; raw chunks,
invalid UTF-8, response and aggregate bounds, retained failure charges; absolute
expiry, flood admission, stream cancellation, repeated cleanup cancellation, cleanup
failure poisoning, explicit close; deterministic policy/budget identity; no production
import/registration. DNS covers actual child timeout/cancellation/reaping, cancellation
during creation, output floods, malformed results, empty inherited environment,
busy/closed admission and eventual cleanup after a shortened test grace expires.

## Trust assumptions and residual limits

- Constructors and dependencies are trusted Core/test code. The caller owns a single
  asyncio event loop, explicitly selects bindings, and must close sessions/resolvers.
  This is not a cross-thread synchronization primitive or a global session limiter.
- Existing endpoint/public-address semantics are reused read-only. Policy semantic
  changes require a version/manifest update; the digest is not an automatic source-code
  integrity hash. Evidence binds the endpoint registry separately from the policy.
- Default HTTPX/httpcore and the system certificate store are trusted. Offline tests
  prove configuration and request identity, not a real remote TLS handshake. TLS
  mismatch/SNI integration remains a later controlled qualification gate.
- DNS trusts the fixed interpreter/stdlib and host resolver/NSS configuration; only
  hostname/port arguments and an empty environment are supplied. No resolver search
  suffix is applied to the absolute FQDN. The subprocess boundary permits cancellation;
  it is not a filesystem/network namespace or OS memory/CPU quota. Resolver-internal
  allocations occur before address-count validation and need later OS resource limits.
- Async deadlines are cooperative; synchronous library/OS operations may exceed them.
  Each cleanup owner allows one additional second. If cleanup cannot be proven, the
  instance rejects further admission and retains its bounded outstanding cleanup task.
  OS-uninterruptible work has no hard real-time kill/reap guarantee. Event-loop shutdown
  before retained cleanup completes is outside the verified lifecycle contract.
- Byte limits cover raw response entity bytes consumed by the broker, not TLS overhead,
  HTTP headers or lower-layer socket/HTTP parser buffers. A failing chunk is charged
  before rejection and is not appended to evidence. Default transport chunks and DNS
  pipe buffers have finite buffering; trusted test seams must also behave cooperatively.
- No page secrets, headers, cookies or query parameters can be supplied through the fetch
  API. A canonical path can still carry sensitive text, and GET can have remote side
  effects: trusted operators must approve suitable origins and paths upstream.
- No renderer headers are emitted because no renderer integration exists. HTML is
  unsanitized untrusted evidence, with no script, download or execution authority.
- No worker registration, browser linkage, new task, capability, persistence migration,
  or production environment setting exists. Parent retains independent review, full
  pytest and exact-head CI/merge gates for any later runtime slice.

## Parent host verification

The serial host gate passed **169 tests**: the 66 new broker/DNS cases plus the
existing API Worker and project-context suites. Diff, Ruff lint and formatting
checks passed. No dependency or production code changes were needed.

Separate controlled renderer probes used the pre-existing read-only browser assets:

- A fixed document was fulfilled over Playwright's pipe connection into the private
  Bubblewrap browser. Chromium native sandboxing remained explicitly enabled.
- A document produced by the new broker through a mock HTTP transport was rendered
  in that browser. Its script could not replace the expected fixture text. This
  links the qualification parts with controlled data, not a production adapter.
- With route interception absent, the private browser could not contact a host
  loopback canary server (zero requests) or read an owned temporary host canary file.
- Repeated cancellation, cancellation during subprocess creation and deadline expiry
  were also checked against actual controlled sleeping children; all were reaped.

The fixture HTML used fixed restrictive CSP and JavaScript-disabled contexts.
An attempted service-worker property read reported a sandbox-policy rejection;
that expected error is retained in evidence, not concealed as an error-free run.
No external site/DNS, user file, credential, or production database was used.
Temporary canaries/listeners were removed; raw logs are in ignored
`runtime/browser-qualification/`. These limited probes do not establish complete
renderer resource control, deployment identity, real-TLS integration, or general
untrusted-page qualification. Those remain required before activation.

Primary references consulted for the implementation and evidence interpretation:

- https://docs.python.org/3.12/library/asyncio-subprocess.html
- https://www.encode.io/httpcore/extensions/
- https://playwright.dev/python/docs/api/class-browser
