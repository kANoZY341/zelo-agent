"""Core persistent-computer egress DNS tests."""

import asyncio

import pytest

from app.computer_egress_dns import ComputerEgressDnsError, ComputerEgressResolver


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "::1",
        "fe80::1",
    ],
)
def test_literal_private_or_scoped_address_is_denied(host):
    resolver = ComputerEgressResolver()

    async def scenario():
        deadline = asyncio.get_running_loop().time() + 1
        with pytest.raises(ComputerEgressDnsError):
            await resolver.resolve(host, 443, deadline)
        await resolver.aclose()

    asyncio.run(scenario())


def test_closed_resolver_fails_admission():
    resolver = ComputerEgressResolver()

    async def scenario():
        await resolver.aclose()
        deadline = asyncio.get_running_loop().time() + 1
        with pytest.raises(
            ComputerEgressDnsError,
            match="computer_egress_dns_admission_denied",
        ):
            await resolver.resolve("example.com", 443, deadline)

    asyncio.run(scenario())
