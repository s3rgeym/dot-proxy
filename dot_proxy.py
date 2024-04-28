#!/usr/bin/env python
# pylint: disable=C,R,W
"""DNS over TLS Proxy Server"""
import argparse
import asyncio
import logging
import ssl
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

__version__ = "0.2.1"
__author__ = "Sergey M"


@dataclass
class DOTClient:
    host: str
    port: int = 853
    reader: asyncio.StreamReader = None
    writer: asyncio.StreamWriter = None

    async def connect(self) -> None:
        sslctx = ssl.create_default_context()
        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port, ssl=sslctx
        )

    async def send_message(self, message: bytes) -> None:
        if not self.writer or self.writer.is_closing():
            await self.connect()

        # Нужно добавить 2 байта в начале - длину запроса
        self.writer.write(int.to_bytes(len(message), 2) + message)
        await self.writer.drain()

    async def recieve_message(self) -> bytes:
        # Ответ так же содержит 2 байта в начале - длину ответа
        return (await self.reader.read(4096))[2:]


class DOTClientPool:
    def __init__(
        self, max_clients: int = 10, *args: Any, **kwargs: Any
    ) -> None:
        self._pool = deque(maxlen=max_clients)  # asyncio.Queue(max_clients)
        for _ in range(max_clients):
            client = DOTClient(*args, **kwargs)
            self._pool.append(client)

    @asynccontextmanager
    async def client(self, timeout: float = 0.1) -> AsyncIterator[DOTClient]:
        client = await self.get_client(timeout)
        logging.debug("get client: 0x%X", id(client))
        try:
            yield client
        finally:
            self.release_client(client)

    async def get_client(self, timeout: float) -> DOTClient:
        while True:
            try:
                return self._pool.popleft()
            except IndexError:
                await asyncio.sleep(timeout)

    def release_client(self, client: DOTClient) -> None:
        self._pool.appendleft(client)


class DOTProxyProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        remote_host: str,
        remote_port: int = 853,
        max_clients: int = 10,
    ) -> None:
        self.client_pool = DOTClientPool(max_clients, remote_host, remote_port)
        self.done = asyncio.get_event_loop().create_future()

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(
        self, data: bytes, addr: tuple[str | Any, int]
    ) -> None:
        logging.debug("received from %s#%i: %s", *addr, data.hex(" "))
        asyncio.create_task(self.process(data, addr))

    async def process(self, data: bytes, addr: tuple[str, int]) -> None:
        async with self.client_pool.client() as client:
            await client.send_message(data)
            message = await client.recieve_message()
            logging.debug("message from remote: %s", message.hex(" "))
            self.transport.sendto(message, addr)

    def error_received(self, exc: Exception) -> None:
        logging.exception(exc)
        if not self.done.done():
            self.done.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logging.exception(exc)
        if not self.done.done():
            self.done.set_result(None)


class NameSpace(argparse.Namespace):
    host: str
    port: int
    remote_host: str
    remote_port: int
    max_clients: int
    verbose: int


def _parse_args(
    argv: Sequence[str] | None,
) -> tuple[argparse.ArgumentParser, NameSpace]:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-H", "--host", help="local host", default="127.0.0.1")
    parser.add_argument(
        "-p", "--port", help="local port", default=9053, type=int
    )
    parser.add_argument(
        "--remote-host", help="remote host", default="1.1.1.1", type=str
    )
    parser.add_argument(
        "--remote-port", help="remote port", default=853, type=int
    )
    parser.add_argument(
        "--max-clients", help="max clients", default=10, type=int
    )
    parser.add_argument(
        "-v", "--verbose", help="be more verbose", action="count", default=0
    )
    args = parser.parse_args(argv, namespace=NameSpace())
    return parser, args


async def run(argv: Sequence[str] | None) -> None:
    parser, args = _parse_args(argv)

    log_level = max(
        logging.DEBUG, logging.CRITICAL - args.verbose * logging.DEBUG
    )

    logging.basicConfig(level=log_level)

    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DOTProxyProtocol(
            remote_host=args.remote_host,
            remote_port=args.remote_port,
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
    try:
        asyncio.run(run(argv))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
