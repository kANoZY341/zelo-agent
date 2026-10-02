# Browser resource-controller host qualification (2 October 2026)

This qualifies host cgroup settings with a **small trusted C fixture**, not Chromium,
not the combined browser/broker runtime, and not a new Worker. UI remains parked.
The production app, database, authority registry and API Worker were not changed.

## Reproducible optional harness

`scripts/qualify_browser_resources.py --execute` compiles the repository-owned
`qualification/fixtures/resource_probe.c` with the existing GCC and builds a scratch
image with no image pull and no build networking. It verifies Docker's create
configuration before starting any pressure case. The C fixture independently
requires UID 65534, PID 1, no-new-privileges and exact cgroup memory/swap/PID/CPU
limits before executing a fixed bounded mode. It has a 12-second safety alarm.
No arbitrary command, user file mount, external networking or credential is accepted.

No privileged mode, host networking/IPC/PID namespace, Docker-socket mount,
capability addition, seccomp disabling, host-service or firewall change was used.
All capabilities were dropped. Filesystem was read-only except the private
8 MiB noexec/nosuid/nodev tmpfs. The explicit run limits were 32 MiB memory,
32 MiB combined memory+swap (zero swap), 12 processes and 0.25 CPU equivalent.

| Case | Observed evidence |
| --- | --- |
| boundary | Read-only root, absent host home/socket, only loopback interface; exact cgroup values checked inside process. |
| pids | 11 child processes admitted; next fork denied with EAGAIN; 11 children reaped. |
| cpu | Quota 25000/100000; CPU throttling counter increased during a 2-second workload. |
| tmpfs | 8388608 bytes written; next write denied with ENOSPC. |
| memory | 32 MiB memory limit, zero swap; attempted allocation above ceiling resulted in cgroup OOM kill (exit 137). |
| cleanup | Owned PID 1 with two controlled children killed; Docker reported Running=false and Pid=0 before container removal. |

Image identity was `sha256:3599d25dd5bfeb0f9cb3694ddaf70a8dd0dab681013902c7eaf4544d3de7c3d0`; compiled fixture SHA-256 was
`670b1d6bdbd27a1fc4dc2f075224fb3a34e699720dd7f36f51940755a01ca639`. Source SHA-256 was `1f0f421ddf0802e02d1b8361035462a0e5a0c3b1dc1ca6f4b415d2ffcb47e855`.
Detailed local output is in ignored `runtime/browser-runtime-qualification/resources-u5zn8ofh/result.json`. All seven uniquely labelled containers
and this invocation's labelled scratch image were removed after the probes.
The first attempted fixture start encountered an unsupported logging combination
(one retained file with compression); it was cleaned up without running pressure
code. Two bounded log files are now requested. No security setting was loosened.

## Verification boundaries

The scratch fixture does not qualify native Chromium sandbox compatibility inside
Docker. It does not link admission, document broker, DNS and renderer in production.
Actual browser artifacts/controller identity and integrated crash/cleanup/resource
behavior still require separate qualification. Docker is only a trusted candidate
host-side resource executor; its socket is never exposed to a worker or page.

Offline `tests/test_browser_resource_probe.py` exercises launch-inspection denials
without Docker or resource-pressure operations. The live harness is explicit opt-in
and not run automatically in the full pytest suite. Primary-source semantics were
checked before testing, not substituted for observed enforcement.

## Primary references checked 2 October 2026

- https://docs.docker.com/engine/containers/resource_constraints/
- https://docs.docker.com/engine/containers/run/
- https://docs.kernel.org/admin-guide/cgroup-v2.html

## Exact follow-up after independent review

The reviewer identified that namespace PID 1 cannot rely on a default-action
SIGALRM. The fixture now installs an explicit async-signal-safe `_exit(124)`
handler before arming its 12-second timer. A seventh real `watchdog` case waits
without an external kill and observed exit 124 under the same cgroup limits.
All seven cases passed again, with no cleanup errors.

The harness now enters cleanup ownership before building/reading the image ID.
Cleanup enumerates only this unpredictable run label, verifies each identity,
attempts remaining removals after a failure, and records unavailable inspection
as unproven rather than absence. Interrupted/failed daemon operations cannot
produce successful cleanup claims. Forceful host death or an unavailable daemon
still requires explicit operator reconciliation; the script makes no hard crash
guarantee. Four additional offline tests cover these exceptional cleanup cases.

Latest source SHA-256: `d7abc84b9c25ec2cf79a8255bae3fb12a9f796b91d1c6b91d9d787c5f8096441`.
Latest static fixture SHA-256: `459763cb7aabfb203d9c75aa74fdbcdfe5fb97650f12bfcd353c60a6ad6d9e04`.
Latest scratch image: `sha256:a8bf30d82146cc99ce91f0e1df0250e8fbad1913c1ebcf913054a4c12a167846` (removed after proof).
Latest local evidence: `runtime/browser-runtime-qualification/resources-o15t9x41/result.json`.
The identifiers above supersede the original six-case provenance for this follow-up.

## Native browser compatibility remains a gate

A separate non-production scratch-image probe copied only the already cached
pinned Chromium headless-shell and its shared libraries. After correcting access
permissions on the disposable image copy, the nonroot no-network/default-seccomp
candidate exited 133 with `No usable sandbox`. The cause has not yet been narrowed
to a specific kernel/runtime restriction. No `--no-sandbox`, privileged mode,
unconfined seccomp or host-network workaround was used; owned images/containers
were removed. This is an explicit failed compatibility gate, not browser activation.

Signal reference: https://man7.org/linux/man-pages/man7/pid_namespaces.7.html
