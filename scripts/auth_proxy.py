#!/usr/bin/env python3
"""Token-gated localhost HTTP/WebSocket reverse proxy."""
import argparse
import asyncio
import hashlib
import http.cookies
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


COOKIE_NAME = "h3_auth"


def valid_token(candidate: str | None, expected: str) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, expected)


def request_token(path: str, headers: dict[str, str]) -> tuple[str | None, bool]:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False

    cookies = http.cookies.SimpleCookie(headers.get("cookie", ""))
    if COOKIE_NAME in cookies:
        return cookies[COOKIE_NAME].value, False

    query = parse_qs(urlsplit(path).query).get("token", [])
    return (query[0] if query else None), bool(query)


def clean_path(path: str) -> str:
    parts = urlsplit(path)
    query = parse_qs(parts.query, keep_blank_values=True)
    query.pop("token", None)
    return urlunsplit(("", "", parts.path or "/", urlencode(query, doseq=True), parts.fragment))


async def send_error(writer: asyncio.StreamWriter) -> None:
    body = b"Unauthorized\n"
    writer.write(
        b"HTTP/1.1 401 Unauthorized\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"WWW-Authenticate: Bearer\r\n"
        b"Cache-Control: no-store\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, target: tuple[str, int], expected: str) -> None:
    try:
        header_bytes = await client_reader.readuntil(b"\r\n\r\n")
        header_text = header_bytes.decode("iso-8859-1")
        lines = header_text.split("\r\n")
        method, path, version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower().strip()] = value.strip()

        token, from_query = request_token(path, headers)
        if not valid_token(token, expected):
            await send_error(client_writer)
            return

        upstream_reader, upstream_writer = await asyncio.open_connection(*target)
        # Consume the token at the auth boundary. Forwarding it upstream is
        # unnecessary and causes redirect-based clients to lose authentication.
        upstream_path = clean_path(path) if from_query else path
        upstream_lines = [f"{method} {upstream_path} {version}"] + lines[1:]
        upstream_writer.write("\r\n".join(upstream_lines).encode("iso-8859-1"))
        await upstream_writer.drain()
        await asyncio.gather(
            relay(client_reader, upstream_writer),
            relay(upstream_reader, client_writer),
        )
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except ConnectionError:
            pass


async def main(listen: int, target: int, expected: str) -> None:
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, ("127.0.0.1", target), expected),
        "0.0.0.0",
        listen,
    )
    print(f"auth proxy listening on 127.0.0.1:{listen} -> 127.0.0.1:{target}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("listen", type=int)
    parser.add_argument("target", type=int)
    args = parser.parse_args()
    import os

    token = os.environ.get("CF_TOKEN", "")
    if not token:
        raise SystemExit("CF_TOKEN is required")
    asyncio.run(main(args.listen, args.target, token))
