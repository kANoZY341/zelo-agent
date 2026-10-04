"""Real stdlib framing over inert bytes; every transport and TLS operation is fake."""

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qualification import media_package_acquisition as m

LOCK = (
    Path(__file__).resolve().parents[1]
    / "qualification/fixtures/media-package-acquisition-lock.json"
)


class TrackedIO(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.closures = 0
        self.close_position = None
        self.line_requests = []

    def readline(self, limit=-1):
        self.line_requests.append(limit)
        return super().readline(limit)

    def close(self):
        if not self.closed:
            self.close_position = self.tell()
            self.closures += 1
        super().close()


class WireSocket:
    def __init__(self, wire):
        self.file = TrackedIO(wire)
        self.sent = []
        self.closures = 0

    def makefile(self, mode):
        assert mode == "rb"
        return self.file

    def sendall(self, raw):
        self.sent.append(bytes(raw))

    def close(self):
        self.closures += 1  # The makefile retains its independent stream lifetime.


def wire(body=b"abc", headers=None, status=b"HTTP/1.1 200 OK", trailing=b""):
    headers = (
        [b"Content-Length: " + str(len(body)).encode(), b"Connection: close"]
        if headers is None
        else headers
    )
    return status + b"\r\n" + b"\r\n".join(headers) + b"\r\n\r\n" + body + trailing


def parsed(raw):
    sock = WireSocket(raw)
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    response = owner.response_factory(sock, method="GET")
    response.begin()
    return owner, response, sock


def test_literal_lock_and_signed_chain():
    raw = LOCK.read_bytes()
    assert len(raw) == 5460
    assert (
        hashlib.sha256(raw).hexdigest()
        == "0c7b800977288ea8beb88f765718d8df42e0a40729ef5bbc6c3d41c2f4f298d6"
    )
    value = m.load_lock(raw)
    assert (
        value["index_proof_sha256"]
        == "199b3b16ee247497b782d78356bb88f3f67ed717401d5cb39ec7a8fc2c0b210d"
    )
    assert [(row["package"], row["bytes"], row["sha256"]) for row in value["packages"]] == [
        (
            "python3.12-minimal",
            2334634,
            "d452689b9660845345a4c3e05e4ad82c082d5474e04031b7aa47f1a6d5610a6e",
        ),
        (
            "libpython3.12-minimal",
            838536,
            "d646ad7112b5adec21ba0e1af015f04ae8ca7f5efdc619622547dd6673c4c14b",
        ),
        (
            "libpython3.12-stdlib",
            2070530,
            "45d3f530ba1f9d6e879ad46b92046fabab13fe50a82450e4f65557a3bbad1489",
        ),
        ("libc6", 3264314, "ff5557d99b51f761c4b7c92368b9cc45565eda17df9bf9eb4b134d09825008be"),
        ("libffi8", 24718, "637e6a7744de08cd331a41f4efd0d24e6ea9064843dea9d1c6ca87bdb5f038a2"),
        ("libseccomp2", 50424, "33fc96f1e008d27c042a3db9bcd16f7f4d49e866f7a3141b758a799328dbdc3f"),
        ("zlib1g", 62988, "84b9cf5752b29c9f92c27cd4c4ba9bbcc70b5ccf9b1b515421a28ae23212e273"),
        ("libexpat1", 98940, "494b8e672f722130c6bca6a7bc4cc31a43ca891a31d60d868bfdd699a3c20b13"),
    ]
    assert sum(row["bytes"] for row in value["packages"]) == 8745084
    assert value["runtime_authority"] is False


def test_lock_hash_precedes_json(monkeypatch):
    monkeypatch.setattr(m.json, "loads", lambda *args: pytest.fail("parsed before lock hash"))
    with pytest.raises(m.Refused, match="lock_pin"):
        m.load_lock(b"untrusted")


@pytest.mark.parametrize(
    "change",
    [
        "host",
        "port",
        "proof",
        "authority",
        "extra",
        "missing",
        "target",
        "archive_bound",
        "suites",
        "request_path",
        "output_name",
        "aggregate",
        "duplicate_suite",
    ],
)
def test_lock_contract_after_pin(change, monkeypatch):
    value = json.loads(LOCK.read_bytes())
    row = value["packages"][0]
    if change == "host":
        value["host"] = "elsewhere.invalid"
    elif change == "port":
        value["port"] = True
    elif change == "proof":
        value["index_proof_sha256"] = "0" * 64
    elif change == "authority":
        value["runtime_authority"] = 0
    elif change == "extra":
        value["extra"] = None
    elif change == "missing":
        value["packages"].pop()
    elif change == "target":
        row["version"] = "3.12.3"
    elif change == "archive_bound":
        row["bytes"] = 4194305
    elif change == "suites":
        row["suites"] = ["untrusted"]
    elif change == "duplicate_suite":
        row["suites"] = ["noble", "noble"]
    elif change == "request_path":
        row["request_path"] = "https://elsewhere.invalid/a"
    elif change == "output_name":
        row["output_name"] = "../outside.deb"
    elif change == "aggregate":
        row["bytes"] -= 1
    raw = json.dumps(value).encode()
    monkeypatch.setattr(m, "LOCK_SHA", hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        m.load_lock(raw)


def test_real_framing_discards_unused_wire_not_certifies_absence():
    body = b"archive bytes"
    extra = b"unused trailing wire"
    owner, response, sock = parsed(wire(body, trailing=extra))
    m.validate_headers(response, len(body))
    assert owner.read1(65536) == body
    assert response.length == 0 and response.fp is None
    assert owner.read1(1) == b""  # Framing EOF, not transport-wire certification.
    assert sock.file.close_position == len(wire(body))
    assert owner.cleanup() is True and sock.file.closures == 1


@pytest.mark.parametrize(
    "status",
    [
        b"HTTP/1.1 100 Continue",
        b"HTTP/1.1 301 Moved",
        b"HTTP/1.1 401 Auth",
        b"HTTP/1.0 200 OK",
        b"HTTP/1.1 404 Missing",
    ],
)
def test_status_refused_before_header_loop(status):
    sock = WireSocket(wire(status=status))
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    response = owner.response_factory(sock, method="GET")
    try:
        with pytest.raises(m.Refused, match="http_status"):
            response.begin()
        assert len(sock.file.line_requests) == 1
    finally:
        owner.cleanup()


@pytest.mark.parametrize(
    "headers",
    [
        [b"Content-Length: 3", b"content-length: 3"],
        [b"Content-Length: 03"],
        [b"Content-Length: +3"],
        [b"Content-Length: 3 "],
        [b"Content-Length: 2"],
        [b"Content-Length: 4"],
        [],
        [b"Content-Length: 3", b"Transfer-Encoding: chunked"],
        [b"Content-Length: 3", b"Content-Encoding: gzip"],
        [b"Content-Length: 3", b"X-Value: first", b" second"],
        [b"Content-Length: 3", b"X-Value: null\x00"],
        [b"Content-Length: 3", b"X-Value: high\xff"],
        [b" Content-Length: 3"],
        [b"Bad header", b"Content-Length: 3"],
        [b"Content-Length: 3", b"X: a", b"x: b"],
    ],
)
def test_header_contract_before_body(headers):
    owner, response, sock = parsed(wire(headers=headers))
    try:
        position = sock.file.tell()
        with pytest.raises(m.Refused):
            m.validate_headers(response, 3)
        assert sock.file.tell() == position
    finally:
        owner.cleanup()


def test_header_line_bound_before_parser():
    raw = wire(headers=[b"Content-Length: 3", b"X: " + b"a" * 8192])
    sock = WireSocket(raw)
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    response = owner.response_factory(sock, method="GET")
    try:
        with pytest.raises(m.Refused, match="header_bytes"):
            response.begin()
        assert max(sock.file.line_requests) <= 8193
    finally:
        owner.cleanup()


def test_literal_header_aggregate_and_count_budgets():
    raw = TrackedIO((b"a" * 8191 + b"\n") * 8 + b"\n")
    stream = m.HeaderStream(raw, float("inf"))
    for _ in range(8):
        assert len(stream.readline(65537)) == 8192
    assert stream.header_bytes == 65536
    with pytest.raises(m.Refused, match="header_bytes"):
        stream.readline(65537)
    stream.close()
    raw = TrackedIO(b"a\n" * 66)
    stream = m.HeaderStream(raw, float("inf"))
    for _ in range(65):
        stream.readline(65537)
    with pytest.raises(m.Refused, match="header_lines"):
        stream.readline(65537)
    assert len(raw.line_requests) == 65
    stream.close()


def test_early_eof_does_not_exhaust_declared_length():
    owner, response, sock = parsed(wire(b"ab", headers=[b"Content-Length: 3"]))
    assert owner.read1(3) == b"ab"
    assert owner.read1(1) == b""
    assert response.length == 1 and response.fp is None
    owner.cleanup()
    assert sock.file.closures == 1


def test_header_deadline_after_blocking_read(monkeypatch):
    clock = [0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])

    class Slow(io.BytesIO):
        def readline(self, limit):
            clock[0] = 301
            return super().readline(limit)

    stream = m.HeaderStream(Slow(b"x\n"), 300)
    with pytest.raises(m.Refused, match="deadline"):
        stream.readline(10)
    stream.close()


def test_close_failure_detaches_and_retains_uncertain_stream():
    calls = []
    raw = SimpleNamespace(close=lambda: (calls.append(1), (_ for _ in ()).throw(OSError()))[-1])
    stream = m.HeaderStream(raw, float("inf"))
    with pytest.raises(OSError):
        stream.close()
    stream.close()
    assert calls == [1] and stream.raw is None and stream.uncertain_raw is raw


def test_read_internal_close_failure_quarantines_owner():
    state = SimpleNamespace(close_ok=True)
    owner = m.NetworkOwner(state, object(), float("inf"))
    owner.response = SimpleNamespace(read1=lambda n: (_ for _ in ()).throw(OSError("hidden close")))
    with pytest.raises(OSError):
        owner.read1(1)
    assert owner.uncertain and state.close_ok is False
    assert owner.cleanup() is False


def test_direct_tls_context_has_no_ambient_factory_or_ca_path(monkeypatch, tmp_path):
    calls = []

    class Context:
        keylog_filename = None

        def __init__(self, protocol):
            calls.append(("new", protocol))

        def set_alpn_protocols(self, value):
            calls.append(("alpn", value))

        def load_verify_locations(self, **kwargs):
            calls.append(("ca", kwargs))

    monkeypatch.setattr(m.ssl, "SSLContext", Context)
    monkeypatch.setattr(
        m.ssl, "create_default_context", lambda *a, **k: pytest.fail("ambient factory")
    )
    sentinel = tmp_path / "must-not-exist"
    monkeypatch.setenv("SSLKEYLOGFILE", str(sentinel))
    context = m.tls_context(b"synthetic CA bytes")
    assert calls == [
        ("new", m.ssl.PROTOCOL_TLS_CLIENT),
        ("alpn", ["http/1.1"]),
        ("ca", {"cadata": "synthetic CA bytes"}),
    ]
    assert context.verify_mode == m.ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version == m.ssl.TLSVersion.TLSv1_2
    assert not sentinel.exists()


@pytest.mark.parametrize("raw", [b"", b"x" * 262145, b"\xff", "text"])
def test_ca_refusal_precedes_context(monkeypatch, raw):
    monkeypatch.setattr(m.ssl, "SSLContext", lambda *a: pytest.fail("constructed context"))
    with pytest.raises(ValueError):
        m.tls_context(raw)


def test_network_uses_real_request_and_response_with_fake_socket(monkeypatch):
    original = m.http.client.HTTPConnection
    sockets = []

    class Connection(original):
        def __init__(self, host, port, *, timeout, context):
            assert (host, port, timeout) == ("archive.ubuntu.com", 443, 10)
            super().__init__(host, port, timeout=timeout)

        def connect(self):
            self.sock = WireSocket(wire())
            sockets.append(self.sock)

    monkeypatch.setattr(m.http.client, "HTTPSConnection", Connection)
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    response = owner.request("/ubuntu/pool/main/x/fixed.deb")
    m.validate_headers(response, 3)
    assert owner.read1(3) == b"abc" and owner.cleanup() is True
    assert b"".join(sockets[0].sent) == (
        b"GET /ubuntu/pool/main/x/fixed.deb HTTP/1.1\r\n"
        b"Host: archive.ubuntu.com\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    )
    assert sockets[0].file.closures == 1 and sockets[0].closures == 1
    with pytest.raises(m.Refused, match="cleanup_retry"):
        owner.cleanup()


def test_response_captured_before_wrapper_installation(monkeypatch):
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    sock = WireSocket(wire())
    monkeypatch.setattr(m, "HeaderStream", lambda *a: (_ for _ in ()).throw(MemoryError()))
    with pytest.raises(MemoryError):
        owner.response_factory(sock, method="GET")
    assert owner.response is not None and owner.response.fp is sock.file
    owner.cleanup()
    assert sock.file.closures == 1


def test_dns_is_fixed_one_address_without_retry(monkeypatch):
    calls = []

    class Socket:
        def settimeout(self, value):
            assert 0 < value <= 10

        def connect(self, address):
            calls.append(("connect", address))

        def close(self):
            calls.append(("close",))

    def resolve(*args):
        assert args == (
            "archive.ubuntu.com",
            443,
            m.socket.AF_INET,
            m.socket.SOCK_STREAM,
            m.socket.IPPROTO_TCP,
        )
        return [
            (m.socket.AF_INET, m.socket.SOCK_STREAM, m.socket.IPPROTO_TCP, "", (ip, 443))
            for ip in ("192.0.2.1", "192.0.2.2")
        ]

    monkeypatch.setattr(m.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(m.socket, "socket", lambda *args: Socket())
    owner = m.NetworkOwner(SimpleNamespace(close_ok=True), object(), float("inf"))
    owner.connect((m.HOST, m.PORT), 10, None)
    assert calls == [("connect", ("192.0.2.1", 443))]
    owner.cleanup()
    assert calls[-1] == ("close",)


def test_connect_failure_closes_once_and_retains_failed_close(monkeypatch):
    calls = []

    class Socket:
        def settimeout(self, value):
            pass

        def connect(self, address):
            raise OSError("connect")

        def close(self):
            calls.append(1)
            raise OSError("close")

    monkeypatch.setattr(
        m.socket,
        "getaddrinfo",
        lambda *args: [
            (m.socket.AF_INET, m.socket.SOCK_STREAM, m.socket.IPPROTO_TCP, "", ("192.0.2.1", 443))
        ],
    )
    sock = Socket()
    monkeypatch.setattr(m.socket, "socket", lambda *args: sock)
    state = SimpleNamespace(close_ok=True)
    owner = m.NetworkOwner(state, object(), float("inf"))
    with pytest.raises(OSError, match="close"):
        owner.connect((m.HOST, m.PORT), 10, None)
    assert owner.uncertain_socket is sock and owner.raw_socket is None
    assert owner.cleanup() is False and calls == [1] and state.close_ok is False
