#!/usr/bin/env python3
"""Offline asserts for the image's pure logic. Run: python3 scripts/selfcheck.py"""

import os
import re
import sys
import tempfile
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
for it in h3_control.selected_items():
    src = h3_control.MODEL_ROOT / it["destination"] / it["name"]
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("x")
assert h3_control.link_models() == (True, [])
for it in h3_control.selected_items():
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
print("selfcheck ok")
