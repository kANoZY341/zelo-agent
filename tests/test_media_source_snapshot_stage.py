"""Synthetic orchestration only: no network, GPG invocation or source authority."""

import hashlib
import lzma
import unittest
from datetime import UTC, datetime

from qualification import media_origin as origin
from qualification import media_source_snapshot as snapshot
from qualification import media_source_snapshot_acquisition as stage


def _source_index():
    descriptor = f" {'a' * 64} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
    tarball = (
        " 7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196 "
        "5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
    )
    return lzma.compress(
        (
            "Package: dpkg\nVersion: 1.22.6ubuntu6.6\nDirectory: pool/main/d/dpkg\n"
            "Checksums-Sha256:\n"
            + descriptor
            + tarball
            + "Checksums-Sha1:\n"
            + f" {'b' * 40} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
            + f" {'c' * 40} 5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
            + "Files:\n"
            + f" {'d' * 32} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
            + f" {'e' * 32} 5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
        ).encode()
    )


def _release(index):
    return (
        "Origin: Ubuntu\nLabel: Ubuntu\nSuite: noble-updates\nCodename: noble\n"
        "Date: Thu, 29 Oct 2026 23:35:03 UTC\nArchitectures: amd64 arm64\n"
        "Components: main restricted universe\nSHA256:\n"
        + f" {'f' * 64} 1234 main/binary-amd64/Packages\n"
        + f" {'e' * 64} 456 main/binary-amd64/Packages.xz\n"
        + f" {hashlib.sha256(index).hexdigest()} {len(index)} main/source/Sources.xz\n"
    ).encode()


def _status():
    stamp = int(datetime(2026, 10, 29, 23, 35, 3, tzinfo=UTC).timestamp())
    return (
        "[GNUPG:] PLAINTEXT 74 0 \n[GNUPG:] NEWSIG\n"
        f"[GNUPG:] KEY_CONSIDERED {origin.FINGERPRINT} 0\n"
        f"[GNUPG:] SIG_ID AAAAAAAAAAAAAAAAAAAAAAAAAAA 2026-10-29 {stamp}\n"
        f"[GNUPG:] GOODSIG {origin.KEY_ID} Example UID\n"
        f"[GNUPG:] VALIDSIG {origin.FINGERPRINT} 2026-10-29 {stamp} 0 4 0 1 10 01 "
        f"{origin.FINGERPRINT}\n"
    ).encode()


def _armor(plaintext):
    return (
        b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
        + plaintext
        + b"-----BEGIN PGP SIGNATURE-----\n\nYWJj\n=YWJj\n-----END PGP SIGNATURE-----\n"
    )


class StageSSequenceTests(unittest.TestCase):
    def setUp(self):
        self.compressed = _source_index()
        self.plaintext = _release(self.compressed)
        self.raw = _armor(self.plaintext)
        self.calls = []

    def fetch(self, path, maximum, expected=None):
        self.calls.append((path, maximum, expected))
        return self.raw if len(self.calls) == 1 else self.compressed

    def verify(self, raw):
        self.assertEqual(raw, self.raw)
        return self.plaintext, _status(), 0, True

    def test_verified_first_input_precedes_exact_hash_bound_second_fetch(self):
        result = stage.assess_snapshot(self.fetch, self.verify)
        base = "/ubuntu/20261030T000000Z/dists/noble-updates/"
        self.assertEqual(
            self.calls,
            [
                (base + "InRelease", origin.RELEASE_BYTES, None),
                (
                    base + snapshot.INDEX_PATH,
                    snapshot.COMPRESSED_BYTES,
                    {
                        "bytes": len(self.compressed),
                        "sha256": hashlib.sha256(self.compressed).hexdigest(),
                    },
                ),
            ],
        )
        self.assertEqual(result["source"]["descriptor"]["sha256"], "a" * 64)
        self.assertFalse(result["runtime_authority"])
        self.assertEqual(result["trust"], "conditional-on-reviewed-fixed-operator")

    def test_bad_signature_status_never_fetches_sources(self):
        with self.assertRaises(origin.OriginError):
            stage.assess_snapshot(
                self.fetch,
                lambda raw: (self.plaintext, _status().replace(b"GOODSIG", b"BADSIG"), 0, True),
            )
        self.assertEqual(len(self.calls), 1)

    def test_changed_sources_bytes_refuse_after_two_requests(self):
        def changed(path, maximum, expected=None):
            value = self.fetch(path, maximum, expected)
            return value if len(self.calls) == 1 else value + b"x"

        with self.assertRaises(snapshot.SourceSnapshotError):
            stage.assess_snapshot(changed, self.verify)
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
