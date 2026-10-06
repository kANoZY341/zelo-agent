"""Offline verification of operator-prepared image filesystem attestation."""

import hashlib
import io
import tarfile

import pytest

from scripts.qualify_browser_renderer import verify_image_archive


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as target:
        for name, content, kind, mode in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.mode = mode
            item.size = len(content) if kind == tarfile.REGTYPE else 0
            item.linkname = "outside" if kind in {tarfile.LNKTYPE, tarfile.SYMTYPE} else ""
            target.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    stream.seek(0)
    return stream


PAYLOAD = b"expected-binary-bytes"
EXPECTED = {"browser-pipe": (len(PAYLOAD), 0o755, hashlib.sha256(PAYLOAD).hexdigest())}
GOOD = [("browser-pipe", PAYLOAD, tarfile.REGTYPE, 0o755)]


def test_exact_image_files_are_attested_not_just_labels():
    result = verify_image_archive(archive(GOOD), EXPECTED)
    assert len(result) == 64
    assert result == verify_image_archive(archive(GOOD), EXPECTED)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [("browser-pipe", b"modified-same-length", tarfile.REGTYPE, 0o755)],
        [("browser-pipe", PAYLOAD, tarfile.REGTYPE, 0o777)],
        GOOD + [("unexpected-executable", b"code", tarfile.REGTYPE, 0o755)],
        GOOD + GOOD,
        [("../browser-pipe", PAYLOAD, tarfile.REGTYPE, 0o755)],
        [("/browser-pipe", PAYLOAD, tarfile.REGTYPE, 0o755)],
        [("browser-pipe", b"", tarfile.SYMTYPE, 0o755)],
        [("browser-pipe", b"", tarfile.LNKTYPE, 0o755)],
        [("browser-pipe", b"", tarfile.FIFOTYPE, 0o755)],
        [("browser-pipe", b"", tarfile.CHRTYPE, 0o755)],
        GOOD + [("etc/hosts", b"payload", tarfile.REGTYPE, 0o755)],
        GOOD + [("opt", b"", tarfile.DIRTYPE, 0o777)],
    ],
)
def test_image_filesystem_mismatch_denied(entries):
    with pytest.raises(ValueError):
        verify_image_archive(archive(entries), EXPECTED)


def test_bounded_nonexecutable_docker_generated_files_are_not_code():
    entries = GOOD + [("etc/hosts", b"127.0.0.1 localhost", tarfile.REGTYPE, 0o644)]
    assert verify_image_archive(archive(entries), EXPECTED) == verify_image_archive(
        archive(GOOD), EXPECTED
    )


def test_same_size_replacement_requires_the_original_content_digest():
    wrong = [("browser-pipe", b"x" * len(PAYLOAD), tarfile.REGTYPE, 0o755)]
    with pytest.raises(ValueError, match="content_mismatch"):
        verify_image_archive(archive(wrong), EXPECTED)


def test_bootstrap_cannot_be_replaced_after_manifest_capture(tmp_path):
    from qualification.artifacts import ArtifactPolicy, capture_manifest
    from scripts.qualify_browser_renderer import expected_image_files

    bootstrap = tmp_path / "browser-pipe"
    bootstrap.write_bytes(PAYLOAD)
    bootstrap.chmod(0o755)
    manifest = capture_manifest(tmp_path, (("bootstrap", "browser-pipe"),), ArtifactPolicy())
    assert expected_image_files(manifest, bootstrap) == EXPECTED
    bootstrap.write_bytes(b"x" * len(PAYLOAD))
    with pytest.raises(ValueError, match="bootstrap_manifest_mismatch"):
        expected_image_files(manifest, bootstrap)


def test_empty_docker_mount_placeholders_are_not_executable_payloads():
    entries = GOOD + [
        (name, b"", tarfile.REGTYPE, 0o755)
        for name in (".dockerenv", "etc/hosts", "etc/hostname", "etc/resolv.conf")
    ]
    assert verify_image_archive(archive(entries), EXPECTED)


def test_docker_console_placeholder_must_be_empty_regular_file():
    assert verify_image_archive(
        archive(GOOD + [("dev/console", b"", tarfile.REGTYPE, 0o755)]), EXPECTED
    )
    for content, kind in [(b"payload", tarfile.REGTYPE), (b"", tarfile.CHRTYPE)]:
        with pytest.raises(ValueError):
            verify_image_archive(archive(GOOD + [("dev/console", content, kind, 0o644)]), EXPECTED)


def test_docker_mount_table_alias_is_exact_and_never_followed():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as target:
        for name, content, kind, mode in GOOD:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            item.mode = mode
            item.type = kind
            target.addfile(item, io.BytesIO(content))
        link = tarfile.TarInfo("etc/mtab")
        link.type = tarfile.SYMTYPE
        link.mode = 0o777
        link.linkname = "/proc/mounts"
        target.addfile(link)
    stream.seek(0)
    assert verify_image_archive(stream, EXPECTED)
    with pytest.raises(ValueError):
        verify_image_archive(archive(GOOD + [("etc/mtab", b"", tarfile.SYMTYPE, 0o777)]), EXPECTED)


def test_integration_diagnostic_requires_explicit_container_opt_in(monkeypatch):
    from scripts import qualify_browser_renderer as probe

    monkeypatch.setattr(
        "sys.argv", ["probe", "--bundle", "/fixture", "--expected-bundle-digest", "a" * 64]
    )
    monkeypatch.setattr(
        probe, "prepare_candidate", lambda *args: pytest.fail("must not prepare image")
    )
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_setup_command_captures_both_streams_and_sanitizes_failure():
    import sys

    from scripts.qualify_browser_renderer import _CommandFailure, command

    assert (
        command(
            [sys.executable, "-I", "-S", "-c", "import sys; print('ok'); sys.stderr.write('note')"]
        )
        == "ok\n"
    )
    with pytest.raises(_CommandFailure, match="^setup_command_failed$") as failure:
        command(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; print('stdout-secret'); sys.stderr.write('do-not-echo'); sys.exit(2)",
            ]
        )
    assert failure.value.returncode == 2
    assert failure.value.stdout == b"stdout-secret\n"
    assert failure.value.stderr == b"do-not-echo"
    assert "stdout-secret" not in str(failure.value)
    assert "do-not-echo" not in str(failure.value)


@pytest.mark.parametrize("target", [1, 2])
def test_setup_command_output_flood_is_bounded_and_reaped(tmp_path, target):
    import os
    import sys

    from scripts.qualify_browser_renderer import command

    pidfile = tmp_path / "pid"
    code = (
        f"import os;open({str(pidfile)!r},'w').write(str(os.getpid()))\n"
        f"while True:os.write({target},b'x'*4096)"
    )
    with pytest.raises(ValueError, match="setup_command_output_limit"):
        command([sys.executable, "-I", "-S", "-c", code])
    pid = int(pidfile.read_text())
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_setup_command_timeout_reaps_the_owned_child(tmp_path):
    import os
    import sys

    from scripts.qualify_browser_renderer import command

    pidfile = tmp_path / "pid"
    code = f"import os,time;open({str(pidfile)!r},'w').write(str(os.getpid()));time.sleep(60)"
    with pytest.raises(TimeoutError, match="setup_command_timeout"):
        command([sys.executable, "-I", "-S", "-c", code], timeout=0.5)
    with pytest.raises(ChildProcessError):
        os.waitpid(int(pidfile.read_text()), os.WNOHANG)


def test_operator_docker_process_ignores_live_daemon_and_credentials(monkeypatch):
    import stat
    from pathlib import Path

    from scripts.qualify_browser_renderer import operator_process

    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "HOME",
        "LD_PRELOAD",
        "ZELO_OPERATOR_TOKEN",
        "GOMAXPROCS",
    ):
        monkeypatch.setenv(name, "live-sentinel")
    with operator_process(["/usr/bin/docker", "image", "inspect", "sha256:" + "a" * 64]) as (
        args,
        env,
    ):
        assert args[:2] == ["/usr/bin/docker", "--host=unix:///var/run/docker.sock"]
        assert "live-sentinel" not in env.values()
        assert env["GOMAXPROCS"] == "2"
        assert "DOCKER_HOST" not in env and "DOCKER_CONTEXT" not in env and "LD_PRELOAD" not in env
        directory = Path(env["DOCKER_CONFIG"])
        assert directory.exists() and list(directory.iterdir()) == []
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert not directory.exists()


@pytest.mark.parametrize(
    "override",
    ["--host=tcp://elsewhere:2375", "--context=live", "--config=/live", "-Htcp://elsewhere:2375"],
)
def test_operator_docker_rejects_daemon_overrides(override):
    from scripts.qualify_browser_renderer import operator_process

    with pytest.raises(ValueError, match="setup_daemon_override_forbidden"):
        with operator_process(["/usr/bin/docker", override, "ps"]):
            pytest.fail("daemon override accepted")


def test_operator_non_docker_command_has_no_inherited_secrets(monkeypatch):
    import sys

    from scripts.qualify_browser_renderer import command

    monkeypatch.setenv("ZELO_OPERATOR_TOKEN", "live-sentinel")
    monkeypatch.setenv("LD_PRELOAD", "/nonexistent/sentinel")
    result = command(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import os;assert 'ZELO_OPERATOR_TOKEN' not in os.environ;"
            "assert 'LD_PRELOAD' not in os.environ;print(os.environ['HOME'])",
        ]
    )
    assert result == "/nonexistent\n"


def test_export_uses_the_same_private_local_docker_process(tmp_path, monkeypatch):
    from pathlib import Path

    from scripts import qualify_browser_renderer as harness

    calls = []

    def export(argv, destination, environment):
        assert Path(environment["DOCKER_CONFIG"]).exists()
        calls.append((argv, destination, environment.copy()))

    monkeypatch.setattr(harness, "_export_bounded", export)
    harness.export_bounded("/usr/bin/docker", "a" * 64, tmp_path / "export.tar")
    argv, destination, environment = calls[0]
    assert argv == ["/usr/bin/docker", "--host=unix:///var/run/docker.sock", "export", "a" * 64]
    assert destination == tmp_path / "export.tar"
    assert not Path(environment["DOCKER_CONFIG"]).exists()
