"""The real-host proof must remain explicit and absent from automatic CI."""

import sys

import pytest

from scripts import qualify_browser_core_binding as proof


def test_core_browser_host_proof_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["proof", "--bundle", "/unused", "--expected-bundle-digest", "0" * 64]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("host preparation was reached without operator opt-in")

    monkeypatch.setattr(proof, "prepare_candidate", forbidden)
    monkeypatch.setattr(proof, "cleanup_owned_setup", forbidden)
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2
