#!/usr/bin/env python3
"""Authenticated H3 status and self-test backend for the existing auth proxy."""

from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


PROJECT_ROOT = Path(os.environ.get("H3_PROJECT_ROOT", "/opt/minimax-h3"))
MODEL_ROOT = Path(os.environ.get("MODEL_ROOT", "/workspace/models"))
COMFYUI_DIR = Path(os.environ.get("COMFYUI_DIR", "/opt/workspace-internal/ComfyUI"))
STATE_PATH = Path(os.environ.get("H3_STATE_PATH", "/workspace/h3-status.json"))
IMAGE_VERSION = os.environ.get("H3_IMAGE_VERSION", "unknown")
if IMAGE_VERSION == "unknown":
    for version_path in (PROJECT_ROOT / "VERSION", Path(__file__).resolve().parent.parent / "VERSION"):
        try:
            IMAGE_VERSION = version_path.read_text(encoding="utf-8").strip()
            if IMAGE_VERSION:
                break
        except OSError:
            continue
COMFY_URL = os.environ.get("COMFYUI_API_BASE", "http://127.0.0.1:18188").rstrip("/")
MODEL_DESTINATIONS = {"diffusion_models", "text_encoders", "vae"}
STATUS_CACHE: dict[str, tuple[float, int | None]] = {}
STATUS_CACHE_LOCK = threading.Lock()
SELFTEST_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def manifest_data() -> dict:
    inline = os.environ.get("MODEL_MANIFEST_JSON", "").strip()
    remote_url = os.environ.get("MODEL_MANIFEST_URL", "").strip()
    path = Path(os.environ.get("MODEL_MANIFEST", str(PROJECT_ROOT / "config" / "model-manifest.json")))
    if inline:
        value = json.loads(inline)
    elif remote_url:
        if urlsplit(remote_url).scheme not in {"http", "https"}:
            raise ValueError("MODEL_MANIFEST_URL must use http or https")
        with urllib.request.urlopen(remote_url, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return {"files": value, "presets": {}}
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise ValueError("model manifest must be a file list or an object containing files")
    return value


def item_name(item: dict) -> str:
    if item.get("name"):
        return str(item["name"])
    from urllib.parse import unquote

    return unquote(urlsplit(str(item.get("url") or item.get("path") or "")).path.rstrip("/").split("/")[-1])


def selected_items() -> list[dict]:
    manifest = manifest_data()
    enabled = [item for item in manifest["files"] if isinstance(item, dict) and item.get("download", True)]
    preset = os.environ.get("MODEL_PRESET", "5090").strip().lower() or "5090"
    presets = manifest.get("presets", {})
    if preset == "full":
        chosen = enabled
    elif preset in presets:
        names = presets[preset]
        by_name = {item_name(item): item for item in enabled}
        missing = [str(name) for name in names if str(name) not in by_name]
        if missing:
            raise ValueError("manifest preset names missing files: " + ", ".join(missing))
        chosen = [by_name[str(name)] for name in names]
    elif preset in {"5090", "6000", "override"}:
        # Legacy list manifests have no named presets. Select their H3 model
        # directories, leaving unrelated optional tools (e.g. LatentSync) out.
        chosen = [item for item in enabled if str(item.get("destination", "")) in MODEL_DESTINATIONS]
    else:
        raise ValueError(f"unknown MODEL_PRESET={preset}")

    result = []
    for item in chosen:
        name = item_name(item)
        destination = str(item.get("destination", ""))
        url = str(item.get("url", ""))
        if not url and item.get("path"):
            url = f"https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/{item['path']}"
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or not destination
            or destination in {".", ".."}
            or "/" in destination
            or "\\" in destination
            or not url.startswith(("https://", "http://"))
        ):
            raise ValueError("selected model manifest entries require a URL, filename, and destination")
        result.append({**item, "name": name, "destination": destination, "url": url})
    return result


def manifest_signature() -> str:
    payload = {
        "version": IMAGE_VERSION,
        "mode": os.environ.get("DOWNLOAD_MODE", "missing"),
        "models": [
            {"name": item["name"], "destination": item["destination"], "url": item["url"]}
            for item in selected_items()
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"phase": "booting", "prepared": False, "last_error": None}


@contextlib.contextmanager
def state_lock():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, STATE_PATH)


def set_phase(phase: str, error: str | None = None, prepared: bool | None = None) -> None:
    with state_lock():
        _set_phase(phase, error, prepared)


def _set_phase(phase: str, error: str | None, prepared: bool | None) -> None:
    state = load_state()
    state["phase"] = phase
    now = time.time()
    if phase == "error":
        state["last_error"] = error
        state["last_error_at"] = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif phase in {"starting_comfy", "ready"}:
        state["last_error"] = None
        state["last_error_at"] = None
    if phase == "booting":
        state["attempts"] = int(state.get("attempts") or 0) + 1
    state["updated_at"] = now
    if prepared is not None:
        state["prepared"] = prepared
    save_state(state)


def expected_bytes(item: dict) -> int | None:
    cache_key = item["url"]
    now = time.monotonic()
    with STATUS_CACHE_LOCK:
        cached = STATUS_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    def fetch_size(entry: dict) -> int | None:
        headers = {"Accept": "application/json"}
        request = urllib.request.Request(entry["url"], method="HEAD", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                value = response.headers.get("Content-Length") or response.headers.get("X-Linked-Size")
                size = int(value) if value and value.isdigit() else None
                return size if size and size > 0 else None
        except (OSError, urllib.error.URLError, ValueError):
            return None

    # Run metadata HEAD requests concurrently so a temporarily blocked host
    # cannot make the status endpoint wait once per model file.
    items = selected_items()
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(items)))) as pool:
        values = list(pool.map(fetch_size, items))
    for entry, size in zip(items, values):
        with STATUS_CACHE_LOCK:
            STATUS_CACHE[entry["url"]] = (time.monotonic() + (3600 if size else 60), size)
    with STATUS_CACHE_LOCK:
        return STATUS_CACHE.get(cache_key, (0, None))[1]


def downloaded_bytes(item: dict) -> int:
    destination = MODEL_ROOT / item["destination"] / item["name"]
    temporary = destination.with_suffix(destination.suffix + ".part")
    total = 0
    for path in (destination, temporary):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def link_models() -> tuple[bool, list[str]]:
    changed = False
    missing = []
    for item in selected_items():
        if item["destination"] not in MODEL_DESTINATIONS:
            continue
        source = MODEL_ROOT / item["destination"] / item["name"]
        target = COMFYUI_DIR / "models" / item["destination"] / item["name"]
        if not source.is_file():
            missing.append(item["name"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.is_symlink() and target.resolve() == source.resolve():
                continue
            if target.exists() and not target.is_symlink():
                if target.resolve() == source.resolve():
                    continue
                raise RuntimeError(f"refusing to replace existing non-symlink model: {target}")
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.symlink_to(source)
            os.replace(temporary, target)
            changed = True
        except OSError as exc:
            raise RuntimeError(f"could not link model {item['name']}: {exc}") from exc
    return changed, missing


def comfyui_health() -> bool:
    try:
        request = urllib.request.Request(f"{COMFY_URL}/system_stats")
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def status_payload() -> dict:
    state = load_state()
    items = selected_items()
    health = comfyui_health()
    phase = state.get("phase", "booting")
    if phase != "error":
        phase = "ready" if state.get("prepared") and health else "starting_comfy" if phase == "ready" else phase
    files = []
    for item in items:
        expected = expected_bytes(item)
        downloaded = downloaded_bytes(item)
        files.append({
            "name": item["name"],
            "destination": item["destination"],
            "expected_bytes": expected,
            "downloaded_bytes": downloaded,
        })
    return {
        "phase": phase,
        "image_version": IMAGE_VERSION,
        "comfyui_health": health,
        "last_error": state.get("last_error"),
        "last_error_at": state.get("last_error_at"),
        "attempts": int(state.get("attempts") or 0),
        "selftest": {**(state.get("selftest") or {}), "running": SELFTEST_LOCK.locked()},
        "files": files,
    }


def comfy_request(path: str, data: bytes | None = None, content_type: str | None = None) -> dict:
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(f"{COMFY_URL}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI returned HTTP {exc.code}: {detail[:1000]}") from exc


def api_workflow(image_name: str, run_id: str) -> dict:
    by_destination = {item["destination"]: item["name"] for item in selected_items()}
    needed = ("diffusion_models", "text_encoders", "vae")
    missing = [destination for destination in needed if destination not in by_destination]
    audio_vae = next(
        (item["name"] for item in selected_items() if item["destination"] == "vae" and "audio" in item["name"].lower()),
        None,
    )
    video_vae = next(
        (item["name"] for item in selected_items() if item["destination"] == "vae" and "audio" not in item["name"].lower()),
        None,
    )
    if missing or not audio_vae or not video_vae:
        raise RuntimeError("manifest must select a diffusion model, text encoder, video VAE, and audio VAE")

    width = 512
    height = 512
    duration_seconds = 3
    frames = max(5, round(duration_seconds * 24))
    frames += (5 - (frames % 17)) % 17
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": by_destination["diffusion_models"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": by_destination["text_encoders"], "type": "minimax", "device": "cpu"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": video_vae}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": audio_vae}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "6": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "audio_vae": ["4", 0],
                "ref_images.ref_image_0": ["5", 0],
                "prompt": "Use <Picture 1> as the reference. A simple colorful test scene with a slow camera push. Include a gentle breeze and one soft bell tone as synchronized native audio.",
                "width": width,
                "height": height,
                "length": frames,
                "ref_image_size": "match",
            },
        },
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["6", 0]}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(run_id.replace("-", "")[:12], 16)}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": 4, "denoise": 1}},
        "11": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["8", 0],
                "guider": ["7", 0],
                "sampler": ["9", 0],
                "sigmas": ["10", 0],
                "latent_image": ["6", 1],
            },
        },
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24, "bit_depth": 8}},
        "15": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["14", 0],
                "filename_prefix": f"h3_selftest/{run_id}",
                "format": "mp4",
                "codec": "h264",
            },
        },
    }


def find_saved_video(outputs: dict) -> Path:
    output_root = (COMFYUI_DIR / "output").resolve()
    candidates = []
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        for key, values in node.items():
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict) and value.get("filename") and value.get("type", "output") == "output":
                    candidates.append(value)
    for item in candidates:
        path = (output_root / item.get("subfolder", "") / item["filename"]).resolve()
        if path.is_relative_to(output_root) and path.is_file():
            return path
    raise RuntimeError("ComfyUI completed but no output video file was found")


# Inputs that are dynamic-combo children and absent from /object_info top level.
DYNAMIC_INPUTS = {"codec"}


def check_object_info(prompt: dict) -> None:
    info = comfy_request("/object_info")
    for node in prompt.values():
        spec = info.get(node["class_type"])
        if not spec:
            raise RuntimeError(f"ComfyUI is missing node {node['class_type']}")
        known = {**spec.get("input", {}).get("required", {}), **spec.get("input", {}).get("optional", {})}
        for key in node["inputs"]:
            if "." not in key and key not in DYNAMIC_INPUTS and key not in known:
                raise RuntimeError(f"ComfyUI node {node['class_type']} has no input {key}")


def run_selftest(run_id: str) -> dict:
    result = {"run_id": run_id, "ok": False, "duration_s": None, "has_audio": False, "output_bytes": 0, "error": None}
    input_copy = None
    video_path = None
    try:
        if status_payload()["phase"] != "ready":
            raise RuntimeError("H3 image is not ready")
        asset = PROJECT_ROOT / "assets" / "selftest-reference.png"
        if not asset.is_file():
            raise RuntimeError("bundled selftest reference image is missing")
        input_dir = COMFYUI_DIR / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        image_name = f"h3-selftest-{run_id}.png"
        input_copy = input_dir / image_name
        shutil.copyfile(asset, input_copy)
        prompt = api_workflow(image_name, run_id)
        check_object_info(prompt)
        prompt_result = comfy_request("/prompt", json.dumps({"prompt": prompt, "client_id": f"h3-selftest-{run_id}"}).encode(), "application/json")
        prompt_id = prompt_result.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt_id")

        timeout = max(60, int(os.environ.get("H3_SELFTEST_TIMEOUT_SECONDS", "1800")))
        deadline = time.monotonic() + timeout
        history = None
        while time.monotonic() < deadline:
            try:
                history_result = comfy_request(f"/history/{quote(prompt_id)}")
            except (OSError, RuntimeError, json.JSONDecodeError):
                time.sleep(2)
                continue
            history = history_result.get(prompt_id)
            if history and history.get("outputs"):
                break
            status = history.get("status", {}) if history else {}
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                detail = json.dumps(messages, ensure_ascii=False)[:1000]
                raise RuntimeError(f"ComfyUI generation failed: {detail}")
            time.sleep(2)
        if not history or not history.get("outputs"):
            raise RuntimeError(f"ComfyUI selftest timed out after {timeout} seconds")

        video_path = find_saved_video(history["outputs"])
        result["output_bytes"] = video_path.stat().st_size
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(video_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        if probe.returncode:
            raise RuntimeError(f"ffprobe failed: {probe.stderr.strip()[:500]}")
        media = json.loads(probe.stdout)
        duration = float(media.get("format", {}).get("duration", 0))
        has_audio = any(stream.get("codec_type") == "audio" for stream in media.get("streams", []))
        result["duration_s"] = round(duration, 3)
        result["has_audio"] = has_audio
        if not 2.5 <= duration <= 4.0:
            raise RuntimeError(f"expected a 3-second video, ffprobe reported {duration:.3f}s")
        if not has_audio:
            raise RuntimeError("ffprobe found no audio stream")
        if result["output_bytes"] <= 0:
            raise RuntimeError("generated video is empty")
        result["ok"] = True
        return result
    except Exception as exc:
        result["error"] = str(exc)[:1200]
        return result
    finally:
        if input_copy:
            input_copy.unlink(missing_ok=True)
        if video_path:
            video_path.unlink(missing_ok=True)


def selftest_job(run_id: str, started_at: float) -> None:
    try:
        result = run_selftest(run_id)
        result["started_at"] = started_at
        result["finished_at"] = time.time()
        with state_lock():
            state = load_state()
            state["selftest"] = result
            save_state(state)
    finally:
        SELFTEST_LOCK.release()


def start_selftest() -> dict | None:
    if not SELFTEST_LOCK.acquire(blocking=False):
        return None
    run_id, started_at = uuid.uuid4().hex, time.time()
    try:
        with state_lock():
            state = load_state()
            state["selftest"] = {"run_id": run_id, "started_at": started_at, "running": True}
            save_state(state)
        threading.Thread(target=selftest_job, args=(run_id, started_at), daemon=True).start()
    except BaseException:
        SELFTEST_LOCK.release()
        raise
    return {"run_id": run_id, "started_at": started_at}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/h3/status":
            self.send_json(404, {"error": "not found"})
            return
        try:
            self.send_json(200, status_payload())
        except Exception as exc:
            self.send_json(200, {
                "phase": "error",
                "image_version": IMAGE_VERSION,
                "comfyui_health": False,
                "last_error": f"status generation failed ({type(exc).__name__})",
                "last_error_at": None,
                "attempts": 0,
                "selftest": {"running": SELFTEST_LOCK.locked()},
                "files": [],
            })

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/h3/selftest":
            self.send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(min(length, 65536))
        started = start_selftest()
        if started:
            self.send_json(202, {"started": True, **started})
        else:
            self.send_json(202, {"started": False, "running": True})


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: h3_control.py {phase|model-files|link-models|signature|is-prepared|mark-prepared|serve}")
    command = sys.argv[1]
    if command == "phase":
        phase = sys.argv[2]
        error = sys.argv[3] if len(sys.argv) > 3 else None
        prepared = None
        if len(sys.argv) > 4:
            prepared = sys.argv[4].strip().lower() == "true"
        set_phase(phase, error=error or None, prepared=prepared)
    elif command == "model-files":
        # Template-provided MODEL_FILES is deliberately ignored: this list is
        # derived from the image-selected manifest entries only.
        print(",".join(item["name"] for item in selected_items()))
    elif command == "link-models":
        changed, missing = link_models()
        mode = os.environ.get("DOWNLOAD_MODE", "missing").strip().lower()
        if missing and mode not in {"skip", "probe"}:
            raise SystemExit("selected manifest models are missing after download: " + ", ".join(missing))
        print(json.dumps({"changed": changed, "missing": missing}))
    elif command == "signature":
        print(manifest_signature())
    elif command == "is-prepared":
        stamp = Path("/workspace/.h3-prepared")
        raise SystemExit(0 if stamp.is_file() and stamp.read_text().strip() == manifest_signature() else 1)
    elif command == "mark-prepared":
        Path("/workspace/.h3-prepared").write_text(manifest_signature() + "\n", encoding="utf-8")
    elif command == "serve":
        server = ThreadingHTTPServer(("127.0.0.1", 18189), Handler)
        server.serve_forever()
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
