"""Synthetic status/text only: no GPG, signatures, network or native execution."""

import hashlib

import pytest

from qualification import media_origin as m


def release():
    return (
        "Origin: Ubuntu\nLabel: Ubuntu\nSuite: noble\nCodename: noble\n"
        "Date: Thu, 25 Apr 2024 15:10:33 UTC\nArchitectures: amd64 arm64\n"
        "Components: main restricted universe multiverse\nSHA256:\n"
        + f" {'a' * 64} 1234 main/binary-amd64/Packages\n"
        + f" {'b' * 64} 456 main/binary-amd64/Packages.xz\n"
    ).encode()


def armor():
    # Deliberately not a real signature; tests establish only wrapper semantics.
    return (
        b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
        + release()
        + b"-----BEGIN PGP SIGNATURE-----\n\nYWJj\n=YWJj\n-----END PGP SIGNATURE-----\n"
    )


def status():
    return (
        "[GNUPG:] PLAINTEXT 74 0 \n[GNUPG:] NEWSIG\n"
        f"[GNUPG:] KEY_CONSIDERED {m.FINGERPRINT} 0\n"
        "[GNUPG:] SIG_ID AAAAAAAAAAAAAAAAAAAAAAAAAAA 2024-04-25 1714057833\n"
        f"[GNUPG:] KEY_CONSIDERED {m.FINGERPRINT} 0\n"
        f"[GNUPG:] GOODSIG {m.KEY_ID} Example UID\n"
        f"[GNUPG:] VALIDSIG {m.FINGERPRINT} 2024-04-25 1714057833 0 4 0 1 10 01 "
        f"{m.FINGERPRINT}\n"
    ).encode()


def accept(**kwargs):
    args = dict(suite="noble", exit_code=0, cleanup_confirmed=True)
    args.update(kwargs)
    return m.accept_release(armor(), release(), status(), **args)


def test_complete_independent_synthetic_oracle_has_no_authority():
    result = accept()
    assert result["input"] == {"bytes": len(armor()), "sha256": hashlib.sha256(armor()).hexdigest()}
    assert result["signature"] == {
        "signer": m.FINGERPRINT,
        "primary": m.FINGERPRINT,
        "created": 1714057833,
        "expires": 0,
        "digest_algorithm": 10,
        "signature_class": "01",
    }
    assert result["release"] == {
        "suite": "noble",
        "dates": {"Date": "Thu, 25 Apr 2024 15:10:33 UTC"},
        "indexes": [
            {"path": m.INDEX_PATHS[0], "bytes": 1234, "sha256": "a" * 64},
            {"path": m.INDEX_PATHS[1], "bytes": 456, "sha256": "b" * 64},
        ],
    }
    for key in (
        "current_key_validity_verified",
        "freshness_verified",
        "package_member_origin_verified",
        "platform_complete",
        "abi_qualified",
        "runtime_authority",
    ):
        assert result[key] is False
    assert result["instruction_trust"] == "none"


@pytest.mark.parametrize("code,cleanup", [(1, True), (-9, True), (False, True), (0, False), (0, 1)])
def test_unsettled_or_failed_invocation_never_parses_output(monkeypatch, code, cleanup):
    monkeypatch.setattr(m, "validate_envelope", lambda _: pytest.fail("premature parsing"))
    with pytest.raises(m.OriginError, match="invocation_unsettled"):
        accept(exit_code=code, cleanup_confirmed=cleanup)


@pytest.mark.parametrize(
    "before,after,code",
    [
        (b"[GNUPG:] PLAINTEXT", b"[FAKE:] PLAINTEXT", "status_prefix"),
        (b"NEWSIG", b"NEWSIG arbitrary", "status_newsig"),
        (b"PLAINTEXT 74", b"PLAINTEXT 62", "status_plaintext"),
        (b" 0\n[GNUPG:] SIG_ID", b" 1\n[GNUPG:] SIG_ID", "status_key"),
        (
            b"SIG_ID AAAAAAAAAAAAAAAAAAAAAAAAAAA",
            b"SIG_ID AAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "status_sig_id",
        ),
        (b"GOODSIG " + m.KEY_ID.encode(), b"GOODSIG 0000000000000000", "status_goodsig"),
        (b"Example UID", b"Example %zz", "status_uid_escape"),
        (b" 0 4 0 1 10 01 ", b" 0 3 0 1 10 01 ", "status_identity"),
        (b" 0 4 0 1 10 01 ", b" 0 4 1 1 10 01 ", "status_identity"),
        (b" 0 4 0 1 10 01 ", b" 0 4 0 17 10 01 ", "status_identity"),
        (b" 0 4 0 1 10 01 ", b" 0 4 0 1 2 01 ", "status_identity"),
        (b" 0 4 0 1 10 01 ", b" 0 4 0 1 10 00 ", "status_identity"),
        (b" 0 4 0 1 10 01 ", b" 1714057832 4 0 1 10 01 ", "status_time"),
        (b"2024-04-25 1714057833 0", b"2024-04-24 1714057833 0", "status_time"),
        (b"2024-04-25 1714057833 0", b"2024-04-25 01714057833 0", "status_time"),
        (b"2024-04-25 1714057833 0", b"2024-02-30 1714057833 0", "status_time"),
    ],
)
def test_status_mutations_have_exact_refusal(before, after, code):
    raw = status()
    assert raw.count(before) == 1
    with pytest.raises(m.OriginError, match=code):
        m.validate_status(raw.replace(before, after))


@pytest.mark.parametrize("index", range(7))
def test_every_required_status_removal_refused_except_redundant_key(index):
    lines = status().splitlines(keepends=True)
    raw = b"".join(lines[:index] + lines[index + 1 :])
    if index == 4:
        assert m.validate_status(raw)["primary"] == m.FINGERPRINT
    else:
        with pytest.raises(m.OriginError):
            m.validate_status(raw)


@pytest.mark.parametrize("index", [0, 1, 3, 5, 6])
def test_status_duplicates_refused(index):
    lines = status().splitlines(keepends=True)
    with pytest.raises(m.OriginError):
        m.validate_status(b"".join(lines[:index] + [lines[index]] + lines[index:]))


@pytest.mark.parametrize("i,j", [(0, 1), (1, 2), (3, 5), (5, 6)])
def test_status_order_refused(i, j):
    lines = status().splitlines(keepends=True)
    lines[i], lines[j] = lines[j], lines[i]
    with pytest.raises(m.OriginError):
        m.validate_status(b"".join(lines))


@pytest.mark.parametrize(
    "name", ["BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "FAILURE"]
)
def test_negative_status_cannot_hide_behind_validsig(name):
    with pytest.raises(m.OriginError, match="status_unknown"):
        m.validate_status(status() + f"[GNUPG:] {name}\n".encode())


@pytest.mark.parametrize("last", [False, True])
def test_signer_and_primary_are_independently_pinned(last):
    raw = status()
    pos = (
        raw.rfind(m.FINGERPRINT.encode())
        if last
        else raw.find(m.FINGERPRINT.encode(), raw.index(b"VALIDSIG"))
    )
    raw = raw[:pos] + b"0" * 40 + raw[pos + 40 :]
    with pytest.raises(m.OriginError, match="status_identity"):
        m.validate_status(raw)


@pytest.mark.parametrize("digest", [8, 9, 10])
def test_only_strong_signature_digest_subset(digest):
    assert (
        m.validate_status(status().replace(b" 1 10 01 ", f" 1 {digest} 01 ".encode()))[
            "digest_algorithm"
        ]
        == digest
    )


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda b: b"x\n" + b, "armor_frame"),
        (lambda b: b + b"x\n", "armor_frame"),
        (lambda b: b + b, "armor_frame"),
        (lambda b: b.replace(b"Hash: SHA512", b"Hash: SHA1"), "armor_frame"),
        (lambda b: b.replace(b"=YWJj\n", b"bad\n"), "armor_crc"),
        (lambda b: b.replace(b"YWJj\n=", b"@@@@\n="), "armor_base64"),
        (lambda b: b.replace(b"Origin: Ubuntu", b"-bad"), "armor_dash"),
        (lambda b: b.replace(b"Origin: Ubuntu", b"- -----BEGIN PGP MESSAGE-----"), "armor_nested"),
        (lambda b: b.replace(b"\n", b"\r\n"), "armor_newlines"),
        (lambda b: b[:-1], "armor_newlines"),
    ],
)
def test_armor_framing_refuses_unsigned_or_ambiguous_material(mutate, code):
    with pytest.raises(m.OriginError, match=code):
        m.validate_envelope(mutate(armor()))


@pytest.mark.parametrize(
    "before,after,code",
    [
        (b"Origin: Ubuntu", b"Origin: Other", "release_identity"),
        (b"Suite: noble", b"Suite: noble-security", "release_identity"),
        (b"Codename: noble", b"Codename: jammy", "release_identity"),
        (b"Label: Ubuntu", b"Label: Ubuntu\nlabel: Ubuntu", "release_field"),
        (b"Label: Ubuntu", b"Label: Ubuntu\nLabel: Ubuntu", "release_field"),
        (b"Label: Ubuntu", b"Label:Ubuntu", "release_field"),
        (b"Label: Ubuntu", b"Label: Ubuntu\n continuation", "release_continuation"),
        (b"Label: Ubuntu", b"Label: Ubuntu\n", "release_blank"),
        (b"Architectures: amd64 arm64", b"Architectures: arm64", "release_architectures"),
        (b"Architectures: amd64 arm64", b"Architectures: amd64 amd64", "release_architectures"),
        (b"Thu, 25 Apr 2024 15:10:33 UTC", b"invalid", "release_date"),
        (b"UTC", b"+0100", "release_date"),
        (b"SHA256:", b"SHA256: value", "release_sha256"),
        (b"a" * 64, b"a" * 63, "release_hash_row"),
        (b"a" * 64, b"A" * 64, "release_hash_row"),
        (b"1234", b"01234", "release_size"),
        (b"1234", b"-1", "release_size"),
        (b"1234", b"134217729", "release_selected_size"),
        (b"456", b"33554433", "release_selected_size"),
        (b"1234", b"0", "release_selected_size"),
        (b"Packages.xz", b"Packages", "release_duplicate_path"),
        (b"Packages.xz", b"Other.xz", "release_indexes_missing"),
        (b"main/binary-amd64/Packages.xz", b"../Packages.xz", "release_path"),
        (b"main/binary-amd64/Packages.xz", b"/Packages.xz", "release_path"),
        (b"main/binary-amd64/Packages.xz", b"https://host/Packages.xz", "release_path"),
        (b"main/binary-amd64/Packages.xz", b"main\\Packages.xz", "release_path"),
    ],
)
def test_release_mutations_have_exact_refusals(before, after, code):
    assert release().count(before) == 1
    with pytest.raises(m.OriginError, match=code):
        m.parse_release(release().replace(before, after), "noble")


@pytest.mark.parametrize("function", [m.validate_status, m.validate_envelope])
@pytest.mark.parametrize(
    "raw", [b"", "", bytearray(b"x\n"), b"x", b"x\x00\n", b"x\t\n", b"x\xff\n"]
)
def test_status_and_armor_reject_wrong_types_and_controls(function, raw):
    with pytest.raises(m.OriginError):
        function(raw)


def test_status_and_release_bounds_before_unbounded_allocations():
    with pytest.raises(m.OriginError, match="status_bytes"):
        m.validate_status(b"x" * 16384 + b"\n")
    with pytest.raises(m.OriginError, match="status_lines"):
        m.validate_status(b"\n" * 17)
    with pytest.raises(m.OriginError, match="status_width"):
        m.validate_status(b"x" * 2049 + b"\n")
    with pytest.raises(m.OriginError, match="release_bytes"):
        m.parse_release(b"x" * 524288 + b"\n", "noble")
    with pytest.raises(m.OriginError, match="release_lines"):
        m.parse_release(b"\n" * 8193, "noble")
    with pytest.raises(m.OriginError, match="release_width"):
        m.parse_release(b"x" * 4097 + b"\n", "noble")


def test_release_row_and_field_count_bounds():
    raw = release() + b"".join(f" {'c' * 64} 1 path{i}\n".encode() for i in range(4095))
    with pytest.raises(m.OriginError, match="release_sha256"):
        m.parse_release(raw, "noble")
    raw = b"".join(f"Field{i}: value\n".encode() for i in range(65))
    with pytest.raises(m.OriginError, match="release_field"):
        m.parse_release(raw, "noble")
