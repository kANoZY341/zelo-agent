# Synthetic native-reader attempt 1 refusal — actual evidence review

Reviewed directly in the continuing ChatGPT session on 5 October 2026. No
independent Codex/model run is claimed.

## Frozen source and matching CI

The attempted source was PR #93 corrected head
`c6127669cda7923c851febe1b4f8530402f58ef8`, tree
`d6eaa8a1fa029cd049384f66d27de5218ba7c8cd`.
Matching CI run `37231757784` passed **4,569 tests with two skips** and 11
warnings. The source review cleared at most one synthetic native-reader run.

## Actual refusal

Exactly one native-reader attempt exists:
`runtime/media-native-reader/native-reader-proof-uvjet91z`.

The retained `cases.json` is an exact pre-build refusal:

```json
{
  "runtime_authority": false,
  "stage": "lease-preflight",
  "status": "refused",
  "support_release_confirmed": false
}
```

Operator exit is 1; stdout is empty. Stderr is bounded and ends with the generic
`reader_native_refused` diagnostic. No successful receipt exists. The private
attempt retains exact `policy.json`, `cases.json`, and an empty private 0700
`docker-config` directory. No image build, setup container, dpkg-deb execution,
synthetic package matrix, cgroup evidence, acquired package read or image decode
is accepted from this attempt.

Independent stdlib-only refusal audit:
`runtime/media-native-reader-refusal-audit-20261005.json`.

Bindings:
- policy SHA256:
  `a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3`;
- policy digest:
  `d111c5d7a7e6ef4f2cece7579f29367a37bbeaea2317f60a10e5676a2e81e999`;
- refusal proof SHA256:
  `027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378`;
- marker SHA256:
  `f88573e617e66156815491696f8a51fed078e07df85658800cc1cb259bb34472`;
- lease:
  `9dd76284f54fe91361c4646fafc97a78c98f5179437345d54f9c815b8ea8bd0f`.

The admission marker intentionally remains nonempty because cleanup/release was
not proven at failure time. The lock is no longer held. Later exact read-only
lease discovery reports zero matching containers and zero matching images, and
the exact lease preflight helper now succeeds. Those later observations do not
retroactively convert the failed attempt into success.

## Concurrent daemon activity

Another project (Aegis) was observed using the same Docker daemon during the
qualification window. Its Chromium image build began around 00:34 UAE time and
was still running during the period in which Zelo's refusal files were created
around 00:36. This is a material environmental confounder because Zelo's failure
occurred in Docker lease preflight.

It is **not proven causation**. The retained Zelo refusal records only the stage,
not the exact Docker command exception. Recovery therefore does not assume that
daemon contention was the cause.

## Disposition

REFUSE attempt 1 as a qualification result. It is permanently **1/2 consumed**.
Do not retry it, delete/reset its directory, rewrite its proof, or manually clear
the admission marker.

Next work is a separately reviewed reconciliation procedure. Reconciliation may
clear only the stale admission fence after exact failed-evidence validation,
exclusive admission lock ownership, zero exact lease-owned Docker resources,
a global Docker-daemon quiet window, repeated zero-resource discovery, durable
pending evidence and exact marker revalidation. The failed proof remains intact.

A later source revision may use attempt 2/2 only after reconciliation, matching
CI and fresh source review. Real acquired packages, Pillow, Core/runtime authority,
UI and later milestones remain uncleared.
