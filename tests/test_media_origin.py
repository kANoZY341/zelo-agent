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


def bad_status():
    return (
        "[GNUPG:] PLAINTEXT 74 0 \n[GNUPG:] NEWSIG\n"
        f"[GNUPG:] KEY_CONSIDERED {m.FINGERPRINT} 0\n"
        f"[GNUPG:] BADSIG {m.KEY_ID} Example UID\n"
    ).encode()


def test_bad_signature_requires_complete_pinned_transcript():
    assert m.validate_bad_signature_status(bad_status()) == {
        "status": "pinned-key-bad-signature",
        "primary": m.FINGERPRINT,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[GNUPG:] FAILURE gpg-exit 2\n",
        b"diagnostic with [GNUPG:] BADSIG " + m.KEY_ID.encode() + b" UID\n",
        bad_status().replace(m.KEY_ID.encode(), b"0" * 16),
        bad_status() + b"[GNUPG:] FAILURE unrelated 1\n",
        bad_status() + bad_status(),
        bad_status().replace(b"NEWSIG\n", b"NEWSIG\n[GNUPG:] NEWSIG\n"),
        bad_status().replace(b"BADSIG", b"GOODSIG"),
        bad_status().replace(b"Example UID", b""),
        status(),
    ],
)
def test_negative_status_rejects_unrelated_incomplete_or_embedded_failure(raw):
    with pytest.raises(m.OriginError):
        m.validate_bad_signature_status(raw)


@pytest.mark.parametrize("size", [0, 1073741825, 1455981187, 2182275917, 999999999999])
def test_unselected_size_is_metadata_only_and_never_returned(size):
    extra = f" {'c' * 64} {size} Contents-amd64\n".encode()
    result = m.parse_release(release() + extra, "noble")
    assert result == m.parse_release(release(), "noble")
    assert all(item["path"] in m.INDEX_PATHS for item in result["indexes"])


@pytest.mark.parametrize("size", ["1000000000000", "0001", "+1", "-1", "1e10"])
def test_unused_declaration_still_requires_bounded_canonical_decimal(size):
    extra = f" {'c' * 64} {size} Contents-amd64\n".encode()
    with pytest.raises(m.OriginError, match="release_size"):
        m.parse_release(release() + extra, "noble")


@pytest.mark.parametrize("suffix,maximum", [("", 134217728), (".xz", 33554432), (".gz", 33554432)])
@pytest.mark.parametrize("position", ["maximum", "over", "zero", "large"])
def test_selected_size_limits_remain_exact_and_positive(suffix, maximum, position):
    amount = {"maximum": maximum, "over": maximum + 1, "zero": 0, "large": 999999999999}[position]
    if suffix:
        raw = release().replace(b"Packages.xz", ("Packages" + suffix).encode())
        raw = raw.replace(b"456 ", str(amount).encode() + b" ")
    else:
        raw = release().replace(b"1234 ", str(amount).encode() + b" ")
    if position == "maximum":
        result = m.parse_release(raw, "noble")
        selected = next(
            item for item in result["indexes"] if item["path"].endswith("Packages" + suffix)
        )
        assert selected["bytes"] == maximum
    else:
        with pytest.raises(m.OriginError, match="release_selected_size"):
            m.parse_release(raw, "noble")


@pytest.mark.parametrize(
    "extra,code",
    [
        (f" {'c' * 63} 999999999999 Contents-amd64\n", "release_hash_row"),
        (f" {'c' * 64} 999999999999 ../Contents-amd64\n", "release_path"),
        (f" {'c' * 64} 999999999999 Contents-amd64\n" * 2, "release_duplicate_path"),
    ],
)
def test_large_unused_rows_keep_hash_path_and_duplicate_checks(extra, code):
    with pytest.raises(m.OriginError, match=code):
        m.parse_release(release() + extra.encode(), "noble")


@pytest.mark.parametrize("path", ["main/dep11/icons-128x128@2.tar.gz", "unused@@literal"])
def test_literal_at_paths_are_discarded_metadata(path):
    extra = f" {'c' * 64} 123 {path}\n".encode()
    assert m.parse_release(release() + extra, "noble") == m.parse_release(release(), "noble")


@pytest.mark.parametrize(
    "path",
    [
        "https://user@host/file",
        "//user@host/file",
        "/icons@2",
        "../icons@2",
        "main/../icons@2",
        "main/./icons@2",
        "main//icons@2",
        "main\\icons@2",
        "main/%2e%2e/icons@2",
        "main/icons@2?query",
        "main/icons@2#fragment",
        "main/icons@2:port",
        "@/" + "/".join(["a"] * 8),
        "@" + ("a" * 128),
    ],
)
def test_literal_at_does_not_enable_unsafe_paths(path):
    extra = f" {'c' * 64} 123 {path}\n".encode()
    with pytest.raises(m.OriginError, match="release_path"):
        m.parse_release(release() + extra, "noble")


def test_duplicate_at_declaration_still_refuses():
    extra = f" {'c' * 64} 123 main/dep11/icons@2.tar.gz\n".encode()
    with pytest.raises(m.OriginError, match="release_duplicate_path"):
        m.parse_release(release() + extra + extra, "noble")


@pytest.mark.parametrize(
    "old,new",
    [
        (b"1234 main/binary-amd64/Packages\n", b"1234 main/binary-amd64/Packages@2\n"),
        (b"456 main/binary-amd64/Packages.xz\n", b"456 main/binary-amd64/Packages@2.xz\n"),
    ],
)
def test_literal_at_cannot_extend_selected_index_names(old, new):
    raw = release()
    assert old in raw
    with pytest.raises(m.OriginError, match="release_indexes_missing"):
        m.parse_release(raw.replace(old, new), "noble")
