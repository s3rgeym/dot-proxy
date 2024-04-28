#!/usr/bin/env python
"""DNS over TLS Proxy Server."""

import argparse
import asyncio
import logging
import secrets
import ssl
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from os import getenv
from typing import Any, Self

__version__ = "0.3.0"
__author__ = "Sergey M"


# https://dnsprivacy.org/public_resolvers/
# На данный момент нельзя создать ssl-соединение с IPv6 адресом
# FIXME: OSError: [Errno 101] Network is unreachable
DOT_SERVERS = [
    # quad9
    "9.9.9.9",
    # "2620:fe::fe",
    "9.9.9.10",
    # "2620:fe::10",
    # cloudflare
    "1.1.1.1",
    "1.0.0.1",
    # "2606:4700:4700::1111",
    # "2606:4700:4700::1001",
    # google
    "8.8.8.8",
    "8.8.4.4",
    # "2001:4860:4860::8888",
    # "2001:4860:4860::8844",
]

DEFAULT_DOT_PORT = 853


@dataclass
class ClientConnection:
    host: str | None = None
    port: int | None = None
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None

    async def connect(self: Self) -> None:
        ctx = ssl.create_default_context()
        # ctx.check_hostname = False
        # ctx.verify_mode = ssl.CERT_NONE

        host = self.host or secrets.choice(DOT_SERVERS)
        port = self.port or DEFAULT_DOT_PORT

        logging.debug("connect to %s#%d", host, port)

        self.reader, self.writer = await asyncio.open_connection(
            host,
            port,
            ssl=ctx,
        )

    async def send_message(self: Self, message: bytes) -> None:
        if not self.writer or self.writer.is_closing():
            await self.connect()

        # Нужно добавить 2 байта в начале - длину запроса
        self.writer.write(int.to_bytes(len(message), 2) + message)
        await self.writer.drain()

    async def receive_message(self: Self) -> bytes:
        # Ответ так же содержит 2 байта в начале - длину ответа
        return (await self.reader.read(4096))[2:]


class ClientConnectionPool:
    def __init__(
        self: Self,
        max_connections: int = 10,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._pool = deque(
            iterable=(
                ClientConnection(*args, **kwargs) for _ in range(max_connections)
            ),
            maxlen=max_connections,
        )

    @asynccontextmanager
    async def get_connection(
        self: Self,
        timeout: float = 0.1,
    ) -> AsyncIterator[ClientConnection]:
        c = await self._get_connection_from_pool(timeout)
        logging.debug("get client connection: 0x%X", id(c))
        try:
            yield c
        finally:
            self.release_connection(c)

    async def _get_connection_from_pool(
        self: Self,
        timeout: float,
    ) -> ClientConnection:
        while True:
            try:
                return self._pool.pop()
            except IndexError:
                await asyncio.sleep(timeout)

    def release_connection(self: Self, client: ClientConnection) -> None:
        """Returm client to the pool."""
        self._pool.append(client)


class DOTProxyProtocol(asyncio.DatagramProtocol):
    def __init__(
        self: Self,
        remote_host: str | None = None,
        remote_port: int | None = None,
        max_connections: int = 10,
    ) -> None:
        self.pool = ClientConnectionPool(
            max_connections,
            remote_host,
            remote_port,
        )
        self.sem = asyncio.Semaphore(max_connections)
        self.done = asyncio.get_event_loop().create_future()

    def connection_made(
        self: Self,
        transport: asyncio.DatagramTransport,
    ) -> None:
        self.transport = transport

    def datagram_received(
        self: Self,
        data: bytes,
        addr: tuple[str | Any, int],
    ) -> None:
        logging.debug("request from %s#%i: %s", *addr, data.hex(" "))
        asyncio.create_task(self.process(data, addr))  # noqa: RUF006

    async def process(
        self: Self,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        async with self.sem, self.pool.get_connection() as conn:
            await conn.send_message(data)
            message = await conn.receive_message()
            logging.debug(
                "reply from %s#%d: %s",
                *conn.writer.get_extra_info("peername"),
                message.hex(" "),
            )
            self.transport.sendto(message, addr)

    def error_received(self: Self, exc: Exception) -> None:
        # logging.exception(exc)
        if not self.done.done():
            self.done.set_exception(exc)

    def connection_lost(self: Self, exc: Exception | None) -> None:  # noqa: ARG002
        if not self.done.done():
            self.done.set_result(None)


class NameSpace(argparse.Namespace):
    host: str
    port: int
    remote_host: str | None
    remote_port: int | None
    max_connections: int
    verbose: int


def _parse_args(
    argv: Sequence[str] | None,
) -> tuple[argparse.ArgumentParser, NameSpace]:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-H",
        "--host",
        help="local host",
        default="127.0.0.1",
    )
    parser.add_argument(
        "-p",
        "--port",
        help="local port",
        default=9053,
        type=int,
    )
    parser.add_argument("--remote-host", help="remote host", type=str)
    parser.add_argument("--remote-port", help="remote port", type=int)
    parser.add_argument(
        "-c",
        "--max-connections",
        help="max client connections",
        default=10,
        type=int,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="be more verbose",
        action="count",
        default=0,
    )
    args = parser.parse_args(argv, namespace=NameSpace())
    return parser, args


def int_or_none(v: str | None) -> int | None:
    if v is None:
        return v
    return int(v)


async def async_main(argv: Sequence[str] | None) -> None:
    parser, args = _parse_args(argv)

    # if not args.remote_host:
    #     logging.warning("")

    log_level = max(
        logging.DEBUG,
        logging.CRITICAL - args.verbose * logging.DEBUG,
    )

    logging.basicConfig(level=log_level)

    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DOTProxyProtocol(
            remote_host=args.remote_host or getenv("DNS"),
            remote_port=args.remote_port or int_or_none(getenv("DNS_PORT")),
            max_connections=args.max_connections,
        ),
        local_addr=(args.host, args.port),
    )

    logging.info("server started at %s:%d", args.host, args.port)

    try:
        await protocol.done
    finally:
        transport.close()


def main(argv: Sequence[str] | None = None) -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(async_main(argv))


if __name__ == "__main__":
    main()
