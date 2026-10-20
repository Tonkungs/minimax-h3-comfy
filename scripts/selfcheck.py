#!/usr/bin/env python3
"""Offline asserts for the image's pure logic. Run: python3 scripts/selfcheck.py"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["H3_STATE_PATH"] = str(Path(tempfile.mkdtemp()) / "state.json")
os.environ["MODEL_MANIFEST"] = str(ROOT / "config" / "model-manifest.json")
os.environ["MODEL_PRESET"] = "5090"
os.environ["COMFYUI_API_BASE"] = "http://127.0.0.1:9"  # unreachable: health=false

import fetch_workflows  # noqa: E402
import h3_control  # noqa: E402

# selected_items: only H3 model dirs, all with name/url/destination
items = h3_control.selected_items()
assert items, "no models selected"
assert all(i["destination"] in h3_control.MODEL_DESTINATIONS and i["url"].startswith("https://") for i in items)
assert not any(i.get("download") is False for i in items)

# path-only manifest entry gets a Hugging Face URL
h3_control.manifest_data.cache_clear()
os.environ["MODEL_MANIFEST_JSON"] = '[{"path":"vae/x.safetensors","destination":"vae"}]'
got = h3_control.selected_items()
assert got[0]["name"] == "x.safetensors" and got[0]["url"].endswith("/resolve/main/vae/x.safetensors"), got
del os.environ["MODEL_MANIFEST_JSON"]
h3_control.manifest_data.cache_clear()
h3_control.expected_bytes = lambda item: None

# status phase mapping + selftest field
h3_control.save_state({"phase": "starting_comfy", "prepared": True, "last_error": None,
                       "selftest": {"ok": True, "error": None}})
status = h3_control.status_payload()
assert status["phase"] == "starting_comfy", status["phase"]  # prepared but ComfyUI unhealthy
assert status["selftest"] == {"ok": True, "error": None, "running": False}, status["selftest"]
h3_control.set_phase("error", "boom")
assert h3_control.status_payload()["phase"] == "error"
h3_control.set_phase("booting", None, False)
assert h3_control.status_payload()["phase"] == "booting"

# attempts / last_error lifecycle
h3_control.save_state({"phase": "booting", "prepared": False, "last_error": None})
h3_control.set_phase("error", "boom")
st = h3_control.load_state()
assert st["last_error"] == "boom" and st["last_error_at"].endswith("Z")
h3_control.set_phase("booting", None, False)
h3_control.set_phase("downloading")
st = h3_control.load_state()
assert st["last_error"] == "boom" and st["attempts"] == 1, st
h3_control.set_phase("starting_comfy", None, True)
st = h3_control.load_state()
assert st["last_error"] is None and st["last_error_at"] is None
pl = h3_control.status_payload()
assert pl["attempts"] == 1 and pl["last_error_at"] is None

# start_selftest correlation: run_id/started_at returned and written to state
h3_control.selftest_job = lambda run_id, started_at: h3_control.SELFTEST_LOCK.release()
started = h3_control.start_selftest()
assert started and len(started["run_id"]) == 32 and started["started_at"]
assert h3_control.load_state()["selftest"] == {**started, "running": True}

# api_workflow passes check_object_info against a ComfyUI-shaped object_info
import json, uuid  # noqa: E402
OBJECT_INFO = json.loads("""{"UNETLoader":["unet_name","weight_dtype"],"CLIPLoader":["clip_name","type","device"],
"VAELoader":["vae_name"],"LoadImage":["image"],
"MiniMaxH3ReferenceToVideo":["clip","vae","audio_vae","prompt","width","height","length","ref_image_size"],
"BasicGuider":["model","conditioning"],"RandomNoise":["noise_seed"],"KSamplerSelect":["sampler_name"],
"BasicScheduler":["model","scheduler","steps","denoise"],
"SamplerCustomAdvanced":["noise","guider","sampler","sigmas","latent_image"],
"VAEDecode":["samples","vae"],"VAEDecodeAudio":["samples","vae"],"CreateVideo":["images","audio","fps","bit_depth"],
"SaveVideo":["video","filename_prefix","format","codec"]}""")
real_request = h3_control.comfy_request
h3_control.comfy_request = lambda path, *a, **k: {
    cls: {"input": {"required": {n: ["X"] for n in names}, "optional": {}}} for cls, names in OBJECT_INFO.items()}
h3_control.check_object_info(h3_control.api_workflow("x.png", uuid.uuid4().hex))
h3_control.comfy_request = real_request

# link_models against temp dirs
tmp = Path(tempfile.mkdtemp())
h3_control.MODEL_ROOT, h3_control.COMFYUI_DIR = tmp / "models", tmp / "comfy"
for it in h3_control.boot_items():
    src = h3_control.MODEL_ROOT / it["destination"] / it["name"]
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("x")
assert h3_control.link_models() == (True, [])
for it in h3_control.boot_items():
    tgt = h3_control.COMFYUI_DIR / "models" / it["destination"] / it["name"]
    assert tgt.is_symlink() and tgt.resolve() == (h3_control.MODEL_ROOT / it["destination"] / it["name"]).resolve()
assert h3_control.link_models() == (False, [])
first = items[0]
tgt = h3_control.COMFYUI_DIR / "models" / first["destination"] / first["name"]
tgt.unlink()
tgt.write_text("real")
try:
    h3_control.link_models()
    raise AssertionError("expected RuntimeError")
except RuntimeError:
    pass
tgt.unlink()
(h3_control.MODEL_ROOT / first["destination"] / first["name"]).unlink()
assert h3_control.link_models()[1] == [first["name"]]

# manifest_signature depends on DOWNLOAD_MODE
sig = h3_control.manifest_signature()
os.environ["DOWNLOAD_MODE"] = "skip"
assert h3_control.manifest_signature() != sig
del os.environ["DOWNLOAD_MODE"]

# video recipes: table, file mapping, unknown -> base
assert set(h3_control.RECIPES) == {"base", "smd8", "smd4vsa"}
def recipe_names(r):
    return sorted(i["name"] for i in h3_control.recipe_items(r))
up = "minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors"
assert recipe_names("base") == [up]
assert recipe_names("smd8") == sorted([up, "SMD-MiniMax-H3-Ref2VA-8step.safetensors"])
assert recipe_names("smd4vsa") == sorted([up, "SMD-MiniMax-H3-Ref2VA-4step.safetensors", "fasth3_vsa_gate.safetensors"])
for r in h3_control.RECIPES:
    for i in h3_control.recipe_items(r):
        assert i["url"].startswith("https://huggingface.co/") and i["destination"] in h3_control.RECIPE_DESTINATIONS
assert {i["destination"] for i in h3_control.recipe_items("smd4vsa")} == {"loras", "latent_upscale_models"}
for raw, want in (("smd8", ("smd8", None)), ("", ("base", None)), ("nope", ("base", "nope")), ("../x", ("base", "../x"))):
    os.environ["H3_RECIPE"] = raw
    assert h3_control.boot_recipe() == want, raw
os.environ.pop("H3_RECIPE")

# prepare-request validation
parse = h3_control.parse_prepare_request
assert parse(b'{"recipe":"smd8"}') == "smd8"
for bad in (b"", b"nope", b"[]", b'{"recipe":"evil.safetensors"}', b'{"recipe":1}', b'{"recipe":null}', b"{}"):
    assert parse(bad) is None, bad

# signature includes the recipe; status fields present
sig_base = h3_control.manifest_signature()
os.environ["H3_RECIPE"] = "smd8"
assert h3_control.manifest_signature() != sig_base
assert any(f["name"].startswith("SMD-") for f in h3_control.status_payload()["files"])
st = h3_control.status_payload()
assert st["recipe"] == "smd8" and st["recipe_fallback"] is None and st["recipes_ready"] == ["base"]  # upscaler created by the link test
assert set(st["prepare"]) == {"recipe", "running", "ok", "error", "started_at", "finished_at"}
os.environ.pop("H3_RECIPE")
assert h3_control.status_payload()["recipe"] == "base"

# start_prepare single-run guard and prepare_job outcome (fake download_models, temp lock)
import threading, types, http.client, json as _json  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

_saved = (h3_control.PREPARE_LOCK_PATH, h3_control.prepare_job, sys.modules.get("download_models"))
h3_control.PREPARE_LOCK_PATH = Path(tempfile.mkdtemp()) / "prepare.lock"
try:
    h3_control.prepare_job = lambda recipe: None  # stub: stays "running"
    assert h3_control.start_prepare("smd8") is True
    assert h3_control.start_prepare("smd4vsa") is False and h3_control.prepare_snapshot()["recipe"] == "smd8"
    h3_control.prepare_job = _saved[1]

    def _boom(*_a, **_k):
        exc = SystemExit(1)
        exc.h3_message = "boom"
        raise exc

    sys.modules["download_models"] = types.SimpleNamespace(fetch_extra=_boom)
    h3_control.prepare_job("smd8")
    snap = h3_control.prepare_snapshot()
    assert snap["running"] is False and snap["ok"] is False and snap["error"] == "boom", snap

    # 409 when downloads are disabled
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h3_control.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for mode, code in (("skip", 409), ("probe", 409), ("missing", 400)):
        os.environ["DOWNLOAD_MODE"] = mode
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("POST", "/h3/prepare", body=b"{}")
        resp = conn.getresponse()
        assert resp.status == code, (mode, resp.status)
        if code == 409:
            assert _json.loads(resp.read()) == {"error": "downloads disabled"}
        conn.close()
    srv.shutdown()
finally:
    os.environ.pop("DOWNLOAD_MODE", None)
    h3_control.PREPARE_LOCK_PATH, h3_control.prepare_job = _saved[0], _saved[1]
    if _saved[2] is None:
        sys.modules.pop("download_models", None)
    else:
        sys.modules["download_models"] = _saved[2]
    h3_control.PREPARE.update(recipe=None, running=False, ok=None, error=None, started_at=None, finished_at=None)

# Dockerfile ARG matches VERSION
m = re.search(r"^ARG H3_IMAGE_VERSION=(\S+)", (ROOT / "Dockerfile").read_text(), re.M)
assert m and m.group(1) == (ROOT / "VERSION").read_text().strip()

# set_qwen_cpu_default: top-level and subgraph CLIPLoader
wf = {
    "nodes": [{"type": "CLIPLoader", "widgets_values": ["a", "minimax", "default"]}],
    "definitions": {"subgraphs": [{"nodes": [{"type": "CLIPLoader", "widgets_values": ["a", "minimax", "default"]}]}]},
}
assert fetch_workflows.set_qwen_cpu_default(wf)
assert wf["nodes"][0]["widgets_values"][2] == "cpu"
assert wf["definitions"]["subgraphs"][0]["nodes"][0]["widgets_values"][2] == "cpu"
assert not fetch_workflows.set_qwen_cpu_default(wf)
assert not fetch_workflows.patch_best_effort({"nodes": None})
assert not fetch_workflows.patch_best_effort([])

# probe output line contract
probe = (ROOT / "scripts" / "h3_download_probe.sh").read_text()
line = re.compile(r"^PROBE_RESULT bytes=\d+ seconds=[\d.]+ mbps=[\d.]+( blocked=1)?$")
assert line.match("PROBE_RESULT bytes=100 seconds=1.5 mbps=0.53")
assert line.match("PROBE_RESULT bytes=0 seconds=60 mbps=0.00 blocked=1")
assert "PROBE_RESULT bytes=%s seconds=%s mbps=%s" in probe and "blocked=1" in probe
# --- TLS proxy ---
import asyncio
import hashlib
import hmac
import http.client
import json
import socket
import ssl
import threading

import auth_proxy  # noqa: E402

der = b"abc"
t = auth_proxy.tls_proof("t0k3n", der)
assert t["cert_sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
assert t["cert_sha256"] == hashlib.sha256(der).hexdigest()
assert t["proof"] == hmac.new(b"t0k3n", b"h3-tls-v1:" + t["cert_sha256"].encode(), hashlib.sha256).hexdigest()
assert re.fullmatch(r"[0-9a-f]{64}", t["proof"]) and t["scheme"] == "h3-tls-v1"
assert auth_proxy.tls_proof("other", der)["proof"] != t["proof"]

for h in ("127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"):
    assert auth_proxy.plain_allowed(h), h
for h in ("10.0.0.1", "172.17.0.1", "::ffff:8.8.8.8", "", "nonsense"):
    assert not auth_proxy.plain_allowed(h), h
assert auth_proxy.PLAIN_REJECT.startswith(b"HTTP/1.1 400 Bad Request") and auth_proxy.PLAIN_REJECT.endswith(b"Use HTTPS\n")
body_len = len(auth_proxy.PLAIN_REJECT.split(b"\r\n\r\n", 1)[1])
assert f"Content-Length: {body_len}".encode() in auth_proxy.PLAIN_REJECT

tls_tmp = Path(tempfile.mkdtemp())
cert_p, key_p = auth_proxy.ensure_cert(tls_tmp / "tls")
assert cert_p.stat().st_size and key_p.stat().st_size and (key_p.stat().st_mode & 0o777) == 0o600
first_pem = cert_p.read_bytes()
assert auth_proxy.ensure_cert(tls_tmp / "tls") == (cert_p, key_p) and cert_p.read_bytes() == first_pem
assert not [p for p in tls_tmp.iterdir() if p.name.startswith(".h3-tls-")]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


seen_requests = []


async def fake_upstream(port, text):
    async def h(r, w):
        seen_requests.append((await r.readuntil(b"\r\n\r\n")).decode("iso-8859-1"))
        w.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(text)}\r\nConnection: close\r\n\r\n{text}".encode())
        await w.drain()
        w.close()
    return await asyncio.start_server(h, "127.0.0.1", port)


async def tls_main(ports, ready):
    stop = loop_box["stop"] = asyncio.Event()
    ups = [await fake_upstream(ports["u"], "upstream-ok"), await fake_upstream(ports["c"], "control-ok")]
    ctx, cder = auth_proxy.make_ctx(cert_p, key_p)
    loop_box["ctx"] = ctx
    task = asyncio.create_task(auth_proxy.serve("127.0.0.1", ports["p"], ports["u"], ports["c"], "selfcheck-token", ctx, cder, ready))
    await stop.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.05)
    for u in ups:
        u.close()


if ssl.OPENSSL_VERSION.startswith("LibreSSL 2."):
    print("selfcheck: live TLS test skipped (python ssl is", ssl.OPENSSL_VERSION + "; cannot serve ECDSA certs)")
else:
    ports = {k: free_port() for k in "ucp"}
    ready, loop_box = threading.Event(), {}


    def run_proxy():
        loop = asyncio.new_event_loop()
        loop_box["loop"] = loop
        loop.run_until_complete(tls_main(ports, ready))
        loop.close()


    th = threading.Thread(target=run_proxy, daemon=True)
    th.start()
    assert ready.wait(5)
    time.sleep(0.2)
    cctx = ssl.create_default_context()
    cctx.check_hostname, cctx.verify_mode = False, ssl.CERT_NONE


    def tls_get(path, headers=None):
        c = http.client.HTTPSConnection("127.0.0.1", ports["p"], context=cctx, timeout=5)
        c.connect()
        ver, peer = c.sock.version(), c.sock.getpeercert(binary_form=True)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        out = (r.status, r.read(), r.getheader("Set-Cookie"), ver, peer)
        c.close()
        return out


    auth = {"Authorization": "Bearer selfcheck-token"}
    st, body, _, ver, peer = tls_get("/h3/tls-proof")
    assert st == 200 and json.loads(body) == auth_proxy.tls_proof("selfcheck-token", peer)
    assert peer == ssl.PEM_cert_to_DER_cert(cert_p.read_text())
    assert ver in ("TLSv1.2", "TLSv1.3")
    assert tls_get("/")[0] == 401
    st, body, _, _, _ = tls_get("/", auth)
    assert st == 200 and b"upstream-ok" in body
    st, body, _, _, _ = tls_get("/h3/status", auth)
    assert st == 200 and b"control-ok" in body
    st, _, cookie, _, _ = tls_get("/?token=selfcheck-token")
    assert st == 302 and "Secure" in cookie
    pc = http.client.HTTPConnection("127.0.0.1", ports["p"], timeout=5)
    pc.request("GET", "/", headers=auth)
    pr = pc.getresponse()
    assert pr.status == 200 and b"upstream-ok" in pr.read()
    pc.close()
    # websocket upgrade kept; proxy credentials stripped, other cookies kept
    seen_requests.clear()
    wc = ssl.create_default_context()
    wc.check_hostname, wc.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", ports["p"]), timeout=5) as raw, wc.wrap_socket(raw) as ws:
        ws.sendall(b"GET /?token=selfcheck-token&x=1 HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   b"Authorization: Bearer selfcheck-token\r\nCookie: a=1; h3_auth=selfcheck-token\r\n\r\n")
        ws.recv(200)
    got = seen_requests[-1]
    assert "Connection: Upgrade" in got and "authorization" not in got.lower(), got
    assert "h3_auth" not in got and "a=1" in got, got
    assert got.startswith("GET /?x=1 ") and "token=" not in got, got  # query token never forwarded

    # plain request from a non-loopback peer is rejected live
    seen_requests.clear()
    a, b = socket.socketpair()
    b.settimeout(5)
    asyncio.run_coroutine_threadsafe(auth_proxy.serve_conn(
        a, "10.0.0.1", loop_box["ctx"], ("127.0.0.1", ports["u"]), ("127.0.0.1", ports["c"]),
        "selfcheck-token", b"{}"), loop_box["loop"])
    b.sendall(b"GET / HTTP/1.1\r\nAuthorization: Bearer selfcheck-token\r\n\r\n")
    assert b.recv(200) == auth_proxy.PLAIN_REJECT
    b.close()
    assert not seen_requests
    loop_box["loop"].call_soon_threadsafe(loop_box["stop"].set)
    th.join(5)

print("selfcheck ok")
