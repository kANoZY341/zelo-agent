"""Operator help and invalid flags must not start a live Worker qualifier."""

import asyncio
import runpy
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    ["scripts.qualify_v015_python_worker_http", "scripts.qualify_v015_api_worker_http"],
)
@pytest.mark.parametrize("argument,expected_code", [("--help", 0), ("--unexpected", 2)])
def test_qualifier_cli_rejects_non_run_arguments_before_dispatch(
    monkeypatch, capsys, module, argument, expected_code
):
    monkeypatch.setattr(sys, "argv", [module, argument])

    def forbidden_dispatch(coroutine):
        coroutine.close()
        raise AssertionError("qualifier_dispatched")

    monkeypatch.setattr(asyncio, "run", forbidden_dispatch)
    with pytest.raises(SystemExit) as result:
        runpy.run_module(module, run_name="__main__")
    assert result.value.code == expected_code
    output = capsys.readouterr()
    assert "usage:" in (output.out if expected_code == 0 else output.err)
