"""The generated guest differs from the reviewed origin guest only in one input pin."""

import unittest

from qualification import media_origin_guest as original
from qualification import media_source_snapshot_guest as guest


class SnapshotGuestTests(unittest.TestCase):
    def test_only_input_record_changes_and_candidate_is_bounded(self):
        digest = "a" * 64
        result = guest.bootstrap(126125, digest)
        old = "INPUTS = " + repr(original.INPUTS) + "\n"
        new = (
            "INPUTS = "
            + repr(
                (
                    {
                        "source": "/opt/zelo-origin.InRelease",
                        "suite": "noble-updates",
                        "bytes": 126125,
                        "sha256": digest,
                        "mode": 0o644,
                    },
                )
            )
            + "\n"
        )
        self.assertEqual(result, original.GUEST_BOOTSTRAP.replace(old, new, 1))
        self.assertEqual(original.GUEST_BOOTSTRAP.count(old), 1)

    def test_invalid_pins_refuse_before_guest_creation(self):
        for size, digest in ((0, "a" * 64), (524289, "a" * 64), (1, "A" * 64)):
            with self.subTest(size=size, digest=digest[:4]):
                with self.assertRaises(ValueError):
                    guest.bootstrap(size, digest)


if __name__ == "__main__":
    unittest.main()
