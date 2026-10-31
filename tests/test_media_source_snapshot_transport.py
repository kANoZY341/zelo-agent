"""Offline transport checks for the fixed Ubuntu snapshot host."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from qualification import media_package_acquisition as transport


class SnapshotTransportTests(unittest.TestCase):
    def test_bounded_inrelease_response_refuses_unsafe_http_framing(self):
        def response(rows, *, status=200, chunked=False, version=11, length=126125):
            headers = SimpleNamespace(items=lambda: rows, defects=[])
            return SimpleNamespace(
                status=status,
                chunked=chunked,
                version=version,
                length=length,
                headers=headers,
            )

        good = response([("Content-Length", "126125"), ("Content-Encoding", "identity")])
        self.assertEqual(transport.bounded_content_length(good, 524288), 126125)
        metadata_duplicate = response(
            [("Content-Length", "126125"), ("Via", "edge-a"), ("via", "edge-b")]
        )
        with self.assertRaises(transport.Refused):
            transport.bounded_content_length(metadata_duplicate, 524288)
        self.assertEqual(
            transport.bounded_content_length(
                metadata_duplicate, 524288, allow_duplicate_metadata=True
            ),
            126125,
        )
        for unsafe_duplicate in (
            [("Content-Length", "126125"), ("content-length", "126125")],
            [
                ("Content-Length", "126125"),
                ("Content-Encoding", "identity"),
                ("content-encoding", "identity"),
            ],
            [
                ("Content-Length", "126125"),
                ("Connection", "close"),
                ("connection", "keep-alive"),
            ],
            [
                ("Content-Length", "126125"),
                ("Transfer-Encoding", "identity"),
                ("transfer-encoding", "identity"),
            ],
            [
                ("Content-Length", "126125"),
                ("Proxy-Connection", "close"),
                ("proxy-connection", "keep-alive"),
            ],
            [("Content-Length", "126125"), ("Content-Range", "a"), ("content-range", "b")],
            [("Content-Length", "126125"), ("Location", "a"), ("location", "b")],
            [("Content-Length", "126125"), ("Trailer", "a"), ("trailer", "b")],
            [("Content-Length", "126125"), ("Upgrade", "a"), ("upgrade", "b")],
            [("Content-Length", "126125"), ("Via", "edge-a"), ("via", "bad\nvalue")],
        ):
            with self.subTest(unsafe_duplicate=unsafe_duplicate):
                with self.assertRaises(transport.Refused):
                    transport.bounded_content_length(
                        response(unsafe_duplicate), 524288, allow_duplicate_metadata=True
                    )
        for unsafe in (
            response([("Content-Length", "126125")], status=302),
            response([("Content-Length", "126125")], chunked=True),
            response([("Content-Length", "126125")], version=10),
            response([("Content-Length", "524289")], length=524289),
            response([("Content-Length", "126125"), ("content-length", "126125")]),
            response([("Content-Length", "126125"), ("Transfer-Encoding", "chunked")]),
            response([("Content-Length", "126125"), ("Content-Encoding", "gzip")]),
            response([]),
        ):
            with self.subTest(unsafe=unsafe.headers.items()):
                with self.assertRaises(transport.Refused):
                    transport.bounded_content_length(unsafe, 524288)

    def test_snapshot_host_is_explicit_and_default_archive_host_is_preserved(self):
        state = SimpleNamespace(close_ok=True)
        default = transport.NetworkOwner(state, object(), float("inf"))
        snapshot = transport.NetworkOwner(state, object(), float("inf"), host="snapshot.ubuntu.com")
        self.assertEqual(default.host, "archive.ubuntu.com")
        self.assertEqual(snapshot.host, "snapshot.ubuntu.com")
        with self.assertRaises(transport.Refused):
            transport.NetworkOwner(state, object(), float("inf"), host="evil.example")

        seen = []

        class FakeConnection:
            def __init__(self, host, port, *, timeout, context):
                seen.append(("connect", host, port, timeout, context))
                self.response_class = None

            def request(self, method, path, *, headers):
                seen.append(("request", method, path, headers))

            def getresponse(self):
                return snapshot.response

        marker = object()
        snapshot.response = marker
        with patch.object(transport.http.client, "HTTPSConnection", FakeConnection):
            self.assertIs(
                snapshot.request("/ubuntu/20261030T000000Z/dists/noble-updates/InRelease"), marker
            )
        self.assertEqual(seen[0][1:3], ("snapshot.ubuntu.com", 443))
        self.assertEqual(
            seen[1][1:3], ("GET", "/ubuntu/20261030T000000Z/dists/noble-updates/InRelease")
        )
        self.assertEqual(seen[1][3]["Host"], "snapshot.ubuntu.com")

    def test_snapshot_dns_and_connect_cannot_switch_to_archive_host(self):
        owner = transport.NetworkOwner(
            SimpleNamespace(close_ok=True), object(), float("inf"), host="snapshot.ubuntu.com"
        )

        class FakeSocket:
            def settimeout(self, value):
                self.timeout = value

            def connect(self, address):
                self.address = address

        sock = FakeSocket()
        dns = [
            (
                transport.socket.AF_INET,
                transport.socket.SOCK_STREAM,
                transport.socket.IPPROTO_TCP,
                "",
                ("192.0.2.1", 443),
            )
        ]
        with (
            patch.object(transport.socket, "getaddrinfo", return_value=dns) as resolver,
            patch.object(transport.socket, "socket", return_value=sock),
        ):
            with self.assertRaises(transport.Refused):
                owner.connect(("archive.ubuntu.com", 443), 10, None)
            self.assertIs(owner.connect(("snapshot.ubuntu.com", 443), 10, None), sock)
        self.assertEqual(resolver.call_args.args[:2], ("snapshot.ubuntu.com", 443))
        self.assertEqual(sock.address, ("192.0.2.1", 443))


if __name__ == "__main__":
    unittest.main()
