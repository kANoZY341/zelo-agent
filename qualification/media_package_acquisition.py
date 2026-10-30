"""Closed archive lock and bounded stdlib HTTP adapter; no operation on import."""

import hashlib
import http.client
import json
import re
import socket
import ssl
import time

from qualification import media_package_index as index

LOCK_SHA = "0c7b800977288ea8beb88f765718d8df42e0a40729ef5bbc6c3d41c2f4f298d6"
INDEX_PROOF_SHA = "199b3b16ee247497b782d78356bb88f3f67ed717401d5cb39ec7a8fc2c0b210d"
INDEX_HEAD = "7cc8849c761e450bf683b7010b7b2f38cf4577f6"
INDEX_MERGE = "19e0be24cb0a60bac1f540f08a27d82082895e9d"
HOST = "archive.ubuntu.com"
SNAPSHOT_HOST = "snapshot.ubuntu.com"
PORT = 443
ARCHIVE_BYTES = 4194304
TOTAL_ARCHIVE_BYTES = 8745084
HEADERS = {"Host": HOST, "Accept-Encoding": "identity", "Connection": "close"}
HEADER_BYTES = 65536
HEADER_LINE_BYTES = 8192
HEADER_LINES = 65
CHUNK_BYTES = 65536
CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
CA_BYTES = 262144
BASELINE = (
    (
        "/usr/lib/python3.12/http/client.py",
        58537,
        "0242200eed2aa47dc2723c99605055c9b792dc197ab36884db0a193772ba99d1",
    ),
    (
        "/usr/lib/python3.12/ssl.py",
        50822,
        "ca7dd38c7d39af5112b9669abffcef3f1bb374fb8fa0a2cd071aea09fce203da",
    ),
    (
        "/usr/lib/python3.12/socket.py",
        37411,
        "b59366f7c35583a274c8031351c146d9c64c4e680adf45d99a3335536a20a6cc",
    ),
)
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}\Z")
_BASENAME = re.compile(r"[A-Za-z0-9_+.-]{1,128}\.deb\Z")
_QUARANTINED = []


class Refused(ValueError):
    """A fixed, bounded stage code; never a peer-controlled diagnostic."""


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise Refused("acquisition_deadline")


def load_lock(raw):
    if type(raw) is not bytes or len(raw) > 8192 or hashlib.sha256(raw).hexdigest() != LOCK_SHA:
        raise Refused("acquisition_lock_pin")
    value = json.loads(raw)
    expected = {
        "schema": "zelo-media-fixed-package-acquisition-lock-v1",
        "index_proof_sha256": INDEX_PROOF_SHA,
        "index_source_head": INDEX_HEAD,
        "index_merge_commit": INDEX_MERGE,
        "host": HOST,
        "port": PORT,
        "scheme": "https",
        "aggregate_bytes": TOTAL_ARCHIVE_BYTES,
        "runtime_authority": False,
    }
    if type(value) is not dict or set(value) != set(expected) | {"packages"}:
        raise Refused("acquisition_lock_shape")
    if any(
        type(value[key]) is not type(wanted) or value[key] != wanted
        for key, wanted in expected.items()
    ):
        raise Refused("acquisition_lock_identity")
    rows = value["packages"]
    if type(rows) is not list or len(rows) != 8:
        raise Refused("acquisition_lock_records")
    record_keys = {"package", "version", "architecture", "filename", "bytes", "sha256", "source"}
    for row, target in zip(rows, index.TARGETS, strict=True):
        if type(row) is not dict or set(row) != record_keys | {
            "suites",
            "request_path",
            "output_name",
        }:
            raise Refused("acquisition_lock_record")
        index._validate_record({key: row[key] for key in record_keys})
        if (row["package"], row["version"]) != target or row["bytes"] > ARCHIVE_BYTES:
            raise Refused("acquisition_lock_target")
        suites = row["suites"]
        if (
            type(suites) is not list
            or not 1 <= len(suites) <= 3
            or any(type(suite) is not str or suite not in index.SUITES for suite in suites)
            or suites != [suite for suite in index.SUITES if suite in suites]
        ):
            raise Refused("acquisition_lock_suites")
        if (
            type(row["request_path"]) is not str
            or row["request_path"] != "/ubuntu/" + row["filename"]
            or type(row["output_name"]) is not str
            or row["output_name"] != row["filename"].rsplit("/", 1)[1]
            or not _BASENAME.fullmatch(row["output_name"])
        ):
            raise Refused("acquisition_lock_paths")
    if sum(row["bytes"] for row in rows) != TOTAL_ARCHIVE_BYTES:
        raise Refused("acquisition_lock_aggregate")
    return value


class HeaderStream:
    """Bound I/O before stdlib decoding; ownership is a stream object, never an FD."""

    def __init__(self, raw, deadline):
        self.raw = raw
        self.uncertain_raw = None
        self.deadline = deadline
        self.header_mode = True
        self.header_bytes = 0
        self.header_lines = 0

    @property
    def closed(self):
        return self.raw is None or self.raw.closed

    def readline(self, limit=-1):
        check_deadline(self.deadline)
        if not self.header_mode or self.raw is None or self.header_lines >= HEADER_LINES:
            raise Refused("acquisition_header_lines")
        bound = min(HEADER_LINE_BYTES + 1, HEADER_BYTES - self.header_bytes + 1)
        if limit >= 0:
            bound = min(bound, limit)
        line = self.raw.readline(bound)
        self.header_lines += 1
        if (
            type(line) is not bytes
            or len(line) > HEADER_LINE_BYTES
            or self.header_bytes + len(line) > HEADER_BYTES
        ):
            raise Refused("acquisition_header_bytes")
        self.header_bytes += len(line)
        check_deadline(self.deadline)
        return line

    def read1(self, count):
        check_deadline(self.deadline)
        if self.header_mode or self.raw is None or not 0 <= count <= CHUNK_BYTES:
            raise Refused("acquisition_body_read")
        chunk = self.raw.read1(count)
        if type(chunk) is not bytes or len(chunk) > count:
            raise Refused("acquisition_body_chunk")
        check_deadline(self.deadline)
        return chunk

    def flush(self):
        if self.raw is not None:
            self.raw.flush()

    def close(self):
        raw, self.raw = self.raw, None
        if raw is not None:
            self.uncertain_raw = raw
            raw.close()
            self.uncertain_raw = None


class FixedResponse(http.client.HTTPResponse):
    def _read_status(self):
        value = super()._read_status()
        if value[0] != "HTTP/1.1" or value[1] != 200:
            raise Refused("acquisition_http_status")
        return value

    def begin(self):
        super().begin()
        self.fp.header_mode = False


def validate_headers(response, size):
    if (
        response.status != 200
        or response.version != 11
        or response.chunked is not False
        or response.headers.defects
    ):
        raise Refused("acquisition_http_framing")
    fields = {}
    for name, value in response.headers.items():
        if (
            type(name) is not str
            or not _TOKEN.fullmatch(name)
            or type(value) is not str
            or len(value) > HEADER_LINE_BYTES
            or any(ord(char) < 32 or ord(char) > 126 for char in value)
            or name.lower() in fields
        ):
            raise Refused("acquisition_http_headers")
        fields[name.lower()] = value
    if (
        fields.get("content-length") != str(size)
        or response.length != size
        or "transfer-encoding" in fields
        or fields.get("content-encoding") not in (None, "identity")
    ):
        raise Refused("acquisition_http_length")


def bounded_content_length(response, maximum):
    """Validate an unknown-size response before reading its first body byte."""
    if type(maximum) is not int or not 0 < maximum <= 4194304:
        raise Refused("acquisition_response_limit")
    if (
        response.status != 200
        or response.version != 11
        or response.chunked is not False
        or response.headers.defects
    ):
        raise Refused("acquisition_http_framing")
    fields = {}
    for name, value in response.headers.items():
        if (
            type(name) is not str
            or not _TOKEN.fullmatch(name)
            or type(value) is not str
            or len(value) > HEADER_LINE_BYTES
            or any(ord(char) < 32 or ord(char) > 126 for char in value)
            or name.lower() in fields
        ):
            raise Refused("acquisition_http_headers")
        fields[name.lower()] = value
    raw = fields.get("content-length", "")
    if (
        not re.fullmatch(r"[1-9][0-9]{0,6}", raw)
        or int(raw) > maximum
        or response.length != int(raw)
        or "transfer-encoding" in fields
        or fields.get("content-encoding") not in (None, "identity")
    ):
        raise Refused("acquisition_http_length")
    return int(raw)


def tls_context(ca_bytes):
    if type(ca_bytes) is not bytes or not 0 < len(ca_bytes) <= CA_BYTES:
        raise Refused("acquisition_ca_bound")
    text = ca_bytes.decode("ascii")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    if context.keylog_filename is not None:
        raise Refused("acquisition_keylog")
    context.load_verify_locations(cadata=text)
    return context


class NetworkOwner:
    """One application cleanup path over inspected stdlib object transitions."""

    def __init__(self, state, context, deadline, *, host=HOST):
        if host not in (HOST, SNAPSHOT_HOST):
            raise Refused("acquisition_host")
        self.state = state
        self.host = host
        self.deadline = deadline
        self.connection = None
        self.response = None
        self.stream = None
        self.raw_socket = None
        self.uncertain_socket = None
        self.cleanup_attempted = False
        self.uncertain = False
        self.context = context

    def unknown(self):
        self.uncertain = True
        self.state.close_ok = False
        _QUARANTINED.append(self)

    def response_factory(self, sock, *args, **kwargs):
        response = FixedResponse(sock, *args, **kwargs)
        self.response = response  # Capture before wrapper installation or begin.
        self.stream = HeaderStream(response.fp, self.deadline)
        response.fp = self.stream
        return response

    def connect(self, address, timeout, source_address):
        if address != (self.host, PORT) or source_address is not None:
            raise Refused("acquisition_connect_identity")
        check_deadline(self.deadline)
        records = socket.getaddrinfo(
            self.host, PORT, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP
        )
        check_deadline(self.deadline)
        if type(records) is not list or not 1 <= len(records) <= 16:
            raise Refused("acquisition_dns_count")
        for record in records:
            if (
                type(record) is not tuple
                or len(record) != 5
                or record[:3] != (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
                or type(record[4]) is not tuple
                or len(record[4]) != 2
                or type(record[4][0]) is not str
                or record[4][1] != PORT
            ):
                raise Refused("acquisition_dns_record")
            socket.inet_pton(socket.AF_INET, record[4][0])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.raw_socket = sock  # Keep until ownership has transferred through TLS.
        try:
            sock.settimeout(min(10, self.deadline - time.monotonic()))
            sock.connect(records[0][4])
            check_deadline(self.deadline)
            return sock
        except BaseException:
            self.uncertain_socket = sock
            self.raw_socket = None
            try:
                sock.close()
            except BaseException:
                self.unknown()
                raise
            self.uncertain_socket = None
            raise

    def request(self, path):
        try:
            check_deadline(self.deadline)
            self.connection = http.client.HTTPSConnection(
                self.host, PORT, timeout=10, context=self.context
            )
            self.connection.response_class = self.response_factory
            self.connection._create_connection = self.connect
            headers = dict(HEADERS, Host=self.host)
            self.connection.request("GET", path, headers=headers)
            # Successful TLS wrapping detached the remembered original socket.
            if self.raw_socket is not None and self.raw_socket.fileno() != -1:
                raise Refused("acquisition_tls_transfer")
            self.raw_socket = None
            check_deadline(self.deadline)
            response = self.connection.getresponse()
            if response is not self.response:
                raise Refused("acquisition_response_identity")
            check_deadline(self.deadline)
            return response
        except BaseException:
            self.unknown()  # Library paths may have performed hidden closes.
            raise

    def read1(self, count):
        try:
            return self.response.read1(count)
        except BaseException:
            self.unknown()  # read1 may have entered hidden EOF/stream close.
            raise

    def cleanup(self):
        if self.cleanup_attempted:
            raise Refused("acquisition_cleanup_retry")
        self.cleanup_attempted = True
        if self.uncertain:
            self.unknown()
            return False
        try:
            if self.response is not None:
                self.response.close()
            if self.connection is not None:
                self.connection.close()
            if self.raw_socket is not None:
                raw, self.raw_socket = self.raw_socket, None
                self.uncertain_socket = raw
                raw.close()
                self.uncertain_socket = None
            check_deadline(self.deadline)
            return True
        except BaseException:
            self.unknown()
            raise
