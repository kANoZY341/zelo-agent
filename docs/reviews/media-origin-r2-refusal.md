# First origin attempt retained

```json
{
  "head": "48600b6ca1bb7d22a9ecaefabd6a4950da9e1766",
  "case": {
    "cancel_requests": 0,
    "case": "network",
    "cleanup_confirmed": true,
    "error": null,
    "evidence": null,
    "exit_code": 22,
    "input_bytes": 126125,
    "input_sha256": "73e9ce9b343db391c104da5f3574adaccd0cd672c450801afa5e6a53de0f2b2c",
    "label": "network",
    "passed": false
  },
  "proof": "runtime/media-origin-proofs/origin-proof-qpba5jjb/cases.json",
  "proof_sha256": "43d78fc777a9cef34d971c8ce18e930f79cfba1c5937c42dc4b3ca51914a065f",
  "operator_log_sha256": "8dfab93ff90fca1cdcb51ff91eaa5deba4e63ab21686a6b283a5e6b382fb33d5",
  "source_review_sha256": "9f3f063a369ba96543f171cb1e1999a7ae4ca64f058581632feab12ba2e8b9c3",
  "origin_attempts_consumed": 1,
  "elf_version_attempts_consumed": 2,
  "elf_loader_attempts_consumed": 4,
  "gpg_executed": false,
  "automatic_retry": false,
  "admission_marker_bytes": 0
}
```

The first network control exited22 before any GPG exec, with cleanup confirmed.
The old bootstrap shared exit22 among ID, capability and mapping checks, so the
record does not identify which check refused. No specific observed map is claimed.
Static bubblewrap source shows the expected single-level map was incorrect for
this invocation: --dev requires a root-mapped setup namespace for devpts, and
--disable-userns creates a second namespace mapping final UID/GID1000 to outer0.
This is a source-supported correction, not an observed diagnostic from the failed
attempt. New bounded refusal diagnostics distinguish ID/capability/map checks.
No automatic retry; exact-source re-review is required before another attempt.
