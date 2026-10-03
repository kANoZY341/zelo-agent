# Media isolated ELF loader metadata — final gates

PR: https://github.com/kANoZY341/zelo-agent/pull/80

Reviewed head:24d8f4114b8007f1e8b51bd8b42318ccb1a1baa2.
Reviewed/merged tree:8e9f48ddace7555503dc20db61639e8d0c44a599.
Merge:f6a8a58413a8bff9c5f85e6ffee9fd3c220083de; exact tree equality verified.
Matching-head PR CI37156454994:success,2,928passed,two supplemented skips,
11existing warnings,715.00seconds. Post-merge CI recorded separately when observed.

1,111 focused Media tests pass, including128 new offline fixtures. Four standalone
supplements pass in19.54seconds; two weakened clean clones hit intended host-auth
and input-binding failures. Full Astra Medium static source/evidence review clears
conditional solely on matching-head CI; successful CI satisfies that condition.
Model/Medium/read-only headers were checked externally by the operator.

Final fixed qualification:37cases/seven pinned input buffers/11,487,248bytes,
all owned cleanup and successful admission release. Proof SHA256
431c21879fc2a1626a417ed5c0e6b38fc0e6b1a30f0be3739d537110462da4ee;
final stdout receipt SHA256ffcd377c0c10c6470f88097b404849a9d132531b9632bf3cf00586bd9b0c79bc.
Policy SHA2568139297845f5d4d160959422da49fed4f14da8c31e0be97df671827d31e628aa.
Accepted attempt73entries/266,317regularbytes. Pending cases file closed before
release; only final stdout confirms release=true and binds its exact hash.

All four attempts retained. First three stopped at libc: generic refusal, dynamic
refusal, then exact DT_LOPROC diagnostic. Each correction received static clearance
before another fixed attempt. Three exact x86-64 PLT constants are now normalized
and recorded as uninterpreted values. No fifth attempt, deletion, cap increase or
marker clearing. Helper-owned close uncertainty conservatively fences admission;
late Lease.finish errors still refuse receipts but may follow marker truncation.

Private artifacts:runtime/media-elf-metadata-final-gates.json, final-binding.json,
final-review.md/log, operator.log, source/helper/diagnostic/tag/x64 review files,
first/second/third failure receipts/operator logs, final focused/standalone/fault
logs, and media-elf-metadata-proofs/metadata-proof-kjd9gzru/cases.json.

This qualifies the isolated metadata tool, not a complete platform or ABI. Section
bodies remain uninterpreted; symbols/versions/dlopen/provenance/native installer,
aggregate cgroups, seccomp and decoder gates remain open. Trusted baseline /usr
runtime may overlap inspected files; supplied buffers never choose an executable.
No Pillow/filter/Docker/Core activation; flags disabled,v0.14.0 preserved,UI61 draft.
