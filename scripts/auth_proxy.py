#!/usr/bin/env python3
"""Token-gated HTTP/WebSocket reverse proxy: TLS for remote clients, plain HTTP only from loopback."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit, urlunsplit


COOKIE_NAME = "h3_auth"
TLS_PROOF_SCHEME = "h3-tls-v1"
TLS_PROOF_PATH = "/h3/tls-proof"
PLAIN_REJECT = (
    b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n"
    b"Content-Length: 10\r\nConnection: close\r\n\r\nUse HTTPS\n"
)


def ensure_cert(tls_dir: Path) -> tuple[Path, Path]:
    cert, key = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert.is_file() and key.is_file() and cert.stat().st_size and key.stat().st_size:
        return cert, key
    tls_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=tls_dir.parent, prefix=".h3-tls-"))
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
             "-nodes", "-keyout", str(tmp / "key.pem"), "-out", str(tmp / "cert.pem"),
             "-days", "3650", "-subj", "/CN=h3-vast"],
            check=True, capture_output=True,
        )
        os.chmod(tmp / "key.pem", 0o600)
        os.rename(tmp, tls_dir)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("openssl cert generation failed: " + (exc.stderr or b"").decode(errors="replace").strip()[-300:]) from exc
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)  # lost the race to another process
        if not (cert.is_file() and key.is_file()):
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return cert, key


def tls_proof(token: str, cert_der: bytes) -> dict:
    digest = hashlib.sha256(cert_der).hexdigest()
    proof = hmac.new(token.encode("utf-8"), f"{TLS_PROOF_SCHEME}:{digest}".encode("ascii"), hashlib.sha256)
    return {"scheme": TLS_PROOF_SCHEME, "cert_sha256": digest, "proof": proof.hexdigest()}


def plain_allowed(host: str) -> bool:
    # Plain HTTP is for the in-container cloudflared quick tunnel (http://localhost:<port>) only.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def valid_token(candidate: str | None, expected: str) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, expected)


def request_token(path: str, headers: dict[str, str]) -> tuple[str | None, bool]:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False

    cookies = http.cookies.SimpleCookie(headers.get("cookie", ""))
    if COOKIE_NAME in cookies:
        return unquote(cookies[COOKIE_NAME].value), False

    query = parse_qs(urlsplit(path).query).get("token", [])
    return (query[0] if query else None), bool(query)


def clean_path(path: str) -> str:
    parts = urlsplit(path)
    query = parse_qs(parts.query, keep_blank_values=True)
    query.pop("token", None)
    return urlunsplit(("", "", parts.path or "/", urlencode(query, doseq=True), parts.fragment))


def strip_credentials(line: str) -> str:
    name, _, value = line.partition(":")
    lname = name.strip().lower()
    if lname == "authorization" and value.strip().lower().startswith("bearer "):
        return ""
    if lname == "cookie":
        kept = [c.strip() for c in value.split(";") if c.strip() and c.split("=", 1)[0].strip() != COOKIE_NAME]
        return f"{name}: {'; '.join(kept)}" if kept else ""
    return line


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


async def set_auth_cookie(writer: asyncio.StreamWriter, path: str, token: str) -> None:
    location = clean_path(path)
    cookie = f"{COOKIE_NAME}={quote(token, safe='')}; Path=/; HttpOnly; Secure; SameSite=Lax"
    writer.write(
        b"HTTP/1.1 302 Found\r\n"
        + f"Location: {location}\r\nSet-Cookie: {cookie}\r\n".encode("iso-8859-1")
        + b"Cache-Control: no-store\r\nContent-Length: 0\r\n\r\n"
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


async def handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target: tuple[str, int],
    control_target: tuple[str, int],
    expected: str,
    proof_body: bytes = b"{}",
) -> None:
    try:
        header_bytes = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), 10)
        header_text = header_bytes.decode("iso-8859-1")
        lines = header_text.split("\r\n")
        method, path, version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower().strip()] = value.strip()

        if method == "GET" and urlsplit(path).path == TLS_PROOF_PATH:
            client_writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n"
                + f"Content-Length: {len(proof_body)}\r\nConnection: close\r\n\r\n".encode()
                + proof_body
            )
            await client_writer.drain()
            return

        token, from_query = request_token(path, headers)
        if not valid_token(token, expected):
            await send_error(client_writer)
            return

        if from_query:
            await set_auth_cookie(client_writer, path, token)
            return

        route_target = control_target if urlsplit(path).path.startswith("/h3/") else target
        upstream_reader, upstream_writer = await asyncio.open_connection(*route_target)
        # Consume the token at the auth boundary: the Bearer header and h3_auth cookie are stripped below.
        upstream_path = clean_path(path) if "token" in parse_qs(urlsplit(path).query) else path
        is_websocket = headers.get("upgrade", "").lower() == "websocket"
        forwarded_headers = [
            line for line in map(strip_credentials, lines[1:])
            if line and not line.lower().startswith(("connection:", "keep-alive:"))
        ]
        forwarded_headers.append("Connection: Upgrade" if is_websocket else "Connection: close")
        upstream_lines = [f"{method} {upstream_path} {version}"] + forwarded_headers
        upstream_writer.write(("\r\n".join(upstream_lines) + "\r\n\r\n").encode("iso-8859-1"))
        await upstream_writer.drain()
        await asyncio.gather(
            relay(client_reader, upstream_writer),
            relay(upstream_reader, client_writer),
        )
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError, asyncio.TimeoutError):
        pass
    finally:
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except ConnectionError:
            pass


async def serve_conn(conn, peer_host, ctx, target, control_target, expected, proof_body) -> None:
    loop = asyncio.get_running_loop()
    try:
        conn.setblocking(False)
        ready = loop.create_future()
        loop.add_reader(conn.fileno(), lambda: ready.done() or ready.set_result(None))
        try:
            await asyncio.wait_for(ready, 10)
        finally:
            loop.remove_reader(conn.fileno())
        first = conn.recv(1, socket.MSG_PEEK)
        is_tls = first == b"\x16"
        if not first or (not is_tls and not plain_allowed(peer_host)):
            if first:
                conn.sendall(PLAIN_REJECT)
            conn.close()
            return
        reader = asyncio.StreamReader(limit=2**16)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.connect_accepted_socket(
            lambda: protocol, conn,
            ssl=ctx if is_tls else None, ssl_handshake_timeout=10 if is_tls else None,
        )
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        await handle(reader, writer, target, control_target, expected, proof_body)
    except (ssl.SSLError, OSError, asyncio.TimeoutError):
        conn.close()


async def serve(host, listen, target, control_target, token, ctx, cert_der, ready_event=None) -> None:
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(False)
    sock.bind((host, listen))
    sock.listen(128)
    loop = asyncio.get_running_loop()
    proof_body = json.dumps(tls_proof(token, cert_der)).encode()
    tasks: set[asyncio.Task] = set()
    print(f"auth proxy listening on {host}:{listen} (tls + loopback-plain) -> 127.0.0.1:{target}", flush=True)
    if ready_event is not None:
        ready_event.set()
    try:
        while True:
            try:
                conn, addr = await loop.sock_accept(sock)
            except OSError as exc:
                print(f"auth proxy accept failed: {exc}", flush=True)
                await asyncio.sleep(0.1)
                continue
            task = asyncio.create_task(serve_conn(
                conn, addr[0], ctx, ("127.0.0.1", target), ("127.0.0.1", control_target), token, proof_body))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        sock.close()
        for task in tasks:
            task.cancel()


def make_ctx(cert: Path, key: Path) -> tuple[ssl.SSLContext, bytes]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ecdh_curve("prime256v1")  # old LibreSSL has no default ECDHE curve
    ctx.load_cert_chain(cert, key)
    return ctx, ssl.PEM_cert_to_DER_cert(cert.read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("listen", type=int)
    parser.add_argument("target", type=int)
    parser.add_argument("control_target", type=int, nargs="?", default=18189)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    token = os.environ.get("CF_TOKEN") or os.environ.get("H3_AUTH_TOKEN", "")
    if not token:
        raise SystemExit("CF_TOKEN is required (H3_AUTH_TOKEN is accepted for legacy templates)")
    cert, key = ensure_cert(Path(os.environ.get("H3_TLS_DIR", "/etc/h3-tls")))  # raises: fail closed
    ctx, cert_der = make_ctx(cert, key)
    asyncio.run(serve(args.host, args.listen, args.target, args.control_target, token, ctx, cert_der))
