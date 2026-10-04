"""Production HTTPSConnection orchestration with entirely synthetic transport objects."""

import io

import pytest

from scripts import qualify_media_package_acquisition as q
from tests import test_media_package_acquisition_operator as fixtures
from tests.test_media_package_acquisition import TrackedIO, WireSocket, wire

operator = fixtures.operator
m = q.m
HTTPS_CONNECTION = m.http.client.HTTPSConnection


@pytest.fixture
def transport(operator, monkeypatch):
    scenario = {"fault": None}
    raw_sockets, tls_sockets, owners, events = [], [], [], []
    original_owner = m.NetworkOwner

    class Owner(original_owner):
        def __init__(self, *args):
            super().__init__(*args)
            owners.append(self)

        def cleanup(self):
            events.append("application cleanup")
            return super().cleanup()

    class Raw:
        def __init__(self):
            self.descriptor = 876543  # Synthetic token; never passed to os.close.
            self.closes = 0

        def fileno(self):
            return self.descriptor

        def detach(self):
            result, self.descriptor = self.descriptor, -1
            events.append("detach")
            return result

        def settimeout(self, value):
            assert 0 < value <= 10

        def connect(self, address):
            assert address == ("192.0.2.1", 443)
            events.append("connect")

        def setsockopt(self, *args):
            assert args == (m.socket.IPPROTO_TCP, m.socket.TCP_NODELAY, 1)

        def close(self):
            self.closes += 1
            self.descriptor = -1

    class Stream(TrackedIO):
        def read1(self, count):
            if scenario["fault"] == "read-interrupt":
                events.append("read interrupt")
                raise KeyboardInterrupt()
            return super().read1(count)

        def close(self):
            events.append("stream close")
            if scenario["fault"] in ("header-close", "body-close"):
                # Model an unknown close outcome. No explicit unknown() in the test.
                raise OSError("synthetic hidden close failure")
            super().close()

    class TLS(WireSocket):
        def close(self):
            events.append("TLS close")
            if scenario["fault"] == "cleanup-interrupt":
                raise KeyboardInterrupt()
            super().close()

    class Context:
        def wrap_socket(self, raw, *, server_hostname):
            assert raw is raw_sockets[-1] and server_hostname == m.HOST
            events.append("wrap")
            if scenario["fault"] == "wrap-before-transfer":
                raise OSError("synthetic wrap failure")
            assert raw.detach() == 876543
            if scenario["fault"] == "wrap-after-transfer":
                raise OSError("synthetic failure after transfer")
            body = next(iter(operator.payloads.values()))
            # A persistent response makes final connection.close the TLS-close owner.
            response = wire(body, headers=[f"Content-Length: {len(body)}".encode()])
            if scenario["fault"] == "header-close":
                response = b"HTTP/1.1 302 Found\r\n\r\n"
            tls = TLS(response)
            tls.file = Stream(response)
            tls_sockets.append(tls)
            return tls

    def socket(*args):
        assert args == (m.socket.AF_INET, m.socket.SOCK_STREAM, m.socket.IPPROTO_TCP)
        raw = Raw()
        raw_sockets.append(raw)
        return raw

    monkeypatch.setattr(m, "NetworkOwner", Owner)
    monkeypatch.setattr(m.http.client, "HTTPSConnection", HTTPS_CONNECTION)
    monkeypatch.setattr(m.socket, "socket", socket)
    monkeypatch.setattr(
        m.socket,
        "getaddrinfo",
        lambda *args: [
            (m.socket.AF_INET, m.socket.SOCK_STREAM, m.socket.IPPROTO_TCP, "", ("192.0.2.1", 443))
        ],
    )
    monkeypatch.setattr(m, "tls_context", lambda ca: Context())
    real_close = q.os.close

    def close(fd):
        assert fd != 876543, "retried synthetic raw socket descriptor"
        return real_close(fd)

    monkeypatch.setattr(q.os, "close", close)
    yield scenario, raw_sockets, tls_sockets, owners, events
    # Explicit fixture teardown of synthetic in-memory buffers, bypassing fault hooks.
    for tls in tls_sockets:
        io.BytesIO.close(tls.file)


def test_production_https_transfer_with_fake_context(operator, transport):
    _, raws, sockets, owners, events = transport
    row = operator.lock["packages"][0]
    path = operator.root / "one-archive"
    path.mkdir(mode=0o700)
    ledger = q.Ledger(path, operator.state)
    fd = q._fetch(operator.state, ledger, row, m.tls_context(b"fake"), float("inf"))
    assert q.os.fstat(fd).st_size == row["bytes"]
    assert len(owners) == len(raws) == len(sockets) == 1
    assert type(owners[0].connection) is HTTPS_CONNECTION
    assert raws[0].fileno() == -1 and raws[0].closes == 0
    assert owners[0].raw_socket is None and owners[0].uncertain is False
    assert events == [
        "connect",
        "wrap",
        "detach",
        "stream close",
        "application cleanup",
        "TLS close",
    ]
    assert sockets[0].closures == sockets[0].file.closures == 1


@pytest.mark.parametrize(
    "fault",
    [
        "wrap-before-transfer",
        "wrap-after-transfer",
        "header-close",
        "body-close",
        "read-interrupt",
        "cleanup-interrupt",
    ],
)
def test_production_failure_transitions_no_receipt_or_restart(operator, transport, fault):
    scenario, raws, sockets, owners, events = transport
    scenario["fault"] = fault
    expected = KeyboardInterrupt if fault.endswith("interrupt") else m.Refused
    with pytest.raises(expected):
        q._qualify()
    assert len(raws) == len(owners) == 1  # No second archive or connect attempt.
    owner = owners[0]
    assert type(owner.connection) is HTTPS_CONNECTION
    assert owner.uncertain is True and operator.state.close_ok is False
    assert owner in m._QUARANTINED
    assert events.count("application cleanup") == (0 if fault == "read-interrupt" else 1)
    assert raws[0].closes == 0  # No object or numeric-FD retry after opaque library failure.
    if fault.startswith("wrap"):
        assert owner.raw_socket is raws[0] and owner.connection.sock is raws[0]
        assert owner.response is None
    else:
        assert owner.response is not None and owner.stream is not None
        if fault in ("header-close", "body-close"):
            assert owner.response.fp is None
            assert owner.stream.raw is None
            assert owner.stream.uncertain_raw is sockets[0].file
            assert events.count("stream close") == 1
        if fault == "read-interrupt":
            assert owner.stream.raw is sockets[0].file
    if fault.endswith("interrupt"):
        assert not any(event[0] == "finish" for event in operator.events)
        assert q._QUARANTINED[-1][0] is operator.state
    else:
        assert operator.events[-1] == ("finish", False)
