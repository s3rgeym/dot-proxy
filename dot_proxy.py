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
DOT_SERVERS = [
    # quad9
    "9.9.9.9",
    "2620:fe:0:0:0:0:0:fe",  # "2620:fe::fe",
    "9.9.9.10",
    "2620:fe:0:0:0:0:0:10",  # "2620:fe::10",
    # cloudflare
    "1.1.1.1",
    "1.0.0.1",
    "2606:4700:4700:0:0:0:0:1111",  # "2606:4700:4700::1111",
    "2606:4700:4700:0:0:0:0:1001",  # "2606:4700:4700::1001",
    # google
    "8.8.8.8",
    "8.8.4.4",
    "2001:4860:4860:0:0:0:0:8888",  # "2001:4860:4860::8888",
    "2001:4860:4860:0:0:0:0:8844",  # "2001:4860:4860::8844",
]

DEFAULT_DOT_PORT = 853


@dataclass
class DOTClient:
    host: str | None = None
    port: int | None = None
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None

    async def connect(self: Self) -> None:
        sslctx = ssl.create_default_context()
        host = self.host or secrets.choice(DOT_SERVERS)
        port = self.port or DEFAULT_DOT_PORT
        logging.debug("connect to %s#%d", host, port)

        # for fam in (socket.AF_INET, socket.AF_INET6):
        #     try:
        self.reader, self.writer = await asyncio.open_connection(
            host,
            port,
            ssl=sslctx,
            verify_ssl=False,
        )
        #    except (socket.gaierror, socket.herror) as ex:
        #        pass

    async def send_message(self: Self, message: bytes) -> None:
        if not self.writer or self.writer.is_closing():
            await self.connect()

        # Нужно добавить 2 байта в начале - длину запроса
        self.writer.write(int.to_bytes(len(message), 2) + message)
        await self.writer.drain()

    async def receive_message(self: Self) -> bytes:
        # Ответ так же содержит 2 байта в начале - длину ответа
        return (await self.reader.read(4096))[2:]


class DOTClientPool:
    def __init__(
        self: Self,
        max_clients: int = 10,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._pool = deque(
            maxlen=max_clients,
        )  # asyncio.Queue(max_clients)
        for _ in range(max_clients):
            client = DOTClient(*args, **kwargs)
            self._pool.append(client)

    @asynccontextmanager
    async def get_client(
        self: Self,
        timeout: float = 0.1,
    ) -> AsyncIterator[DOTClient]:
        client = await self._get_client_from_pool(timeout)
        logging.debug("get client: 0x%X", id(client))
        try:
            yield client
        finally:
            self.release_client(client)

    async def _get_client_from_pool(
        self: Self,
        timeout: float,
    ) -> DOTClient:
        while True:
            try:
                return self._pool.pop()
            except IndexError:
                await asyncio.sleep(timeout)

    def release_client(self: Self, client: DOTClient) -> None:
        """Returm client to the pool."""
        self._pool.append(client)


class DOTProxyProtocol(asyncio.DatagramProtocol):
    def __init__(
        self: Self,
        remote_host: str | None = None,
        remote_port: int | None = None,
        max_clients: int = 10,
    ) -> None:
        self.client_pool = DOTClientPool(
            max_clients,
            remote_host,
            remote_port,
        )
        self.sem = asyncio.Semaphore(max_clients)
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
        async with self.sem, self.client_pool.get_client() as client:
            await client.send_message(data)
            message = await client.receive_message()
            logging.debug(
                "reply from %s#%d: %s",
                *client.writer.get_extra_info("peername"),
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
    max_clients: int
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
        "--max-clients",
        help="max clients",
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
            max_clients=args.max_clients,
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
