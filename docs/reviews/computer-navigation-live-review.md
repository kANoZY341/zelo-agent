# PR #99 matching-source live navigation review — 7 October 2026

Direct continuing-ChatGPT review. No independent Codex/model execution is
claimed.

## Source and CI binding

Qualified code head:
`4d4112cde4a58b782ed92757b7bccdcfde8d8d98`.
Tree:
`97afc43ce3e3530e8fccfd1db3e5a42dec3b1ddf`.
Matching CI run `37618948869` passed **4,768 tests with two skips** plus Ruff
lint/format and diff checks.

The first launcher invocation failed before importing project code because
`PYTHONPATH` was omitted. It created no Docker/network/Agent resource and is not
qualification evidence. The corrected launcher used the unchanged frozen source
with `PYTHONPATH=.`.

## Live proof

The bounded disposable proof used the exact pinned PR #98 images and internal
`172.30.253.0/24` topology. It did not pull or modify images.

Observed:
- Agent computer remained on the internal-only network with no host port;
- neither host computer token nor supervisor token entered the Agent container;
- direct `curl --noproxy '*'` to the public site failed;
- named endpoint `example_site=https://example.com` navigation succeeded through
  the per-Agent authenticated Core proxy;
- final URL remained HTTPS on exact `example.com:443` with no userinfo;
- returned page title/text types and bounds validated without retaining content in
  the evidence receipt;
- a second navigation caused real OpenBot human takeover after the browser
  response; Core's post-response control check refused the result with
  `computer_human_control_active`;
- handback returned control to the bot;
- browser permission was then revoked and the next queued navigation cancelled
  with `computer_execution_spec_invalid` and **zero attempts**;
- cleanup reported no errors and independent post-check found no proof
  container/network/listener.

Sanitized retained receipt:
`runtime/pr99-live-receipt-4d4112c.json`, SHA-256
`6a62863505da2dbd162f59894e52b3ea4f8db8f525fc0c705491a7fb4cc0761e`.

## Independent audit

The stdlib-only auditor performed no project imports, Docker or network actions.
It accepted source identity, pinned images, final origin, direct-egress denial,
token absence, takeover refusal, permission-revocation zero-attempt result and
empty cleanup report.

Audit:
`runtime/pr99-live-audit-4d4112c.json`, SHA-256
`636234e9aaff76571e68b6b6cb51494951a12a6adc09ae89b10510fa04e55a5f`.

No blocking finding remains in named-endpoint navigation scope. A docs-only
evidence-binding commit may follow; it must not change the qualified application
source. Merge still requires exact-head CI/review of that documentation-bearing
head. This proof grants no click/type/key/scroll, arbitrary URL, workspace write,
shell, credential, screenshot-context, host-fallback or UI authority.
