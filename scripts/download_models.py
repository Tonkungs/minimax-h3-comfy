#!/usr/bin/env python3
"""Download selected Comfy-Org MiniMax H3 files into ComfyUI folders."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from huggingface_hub import HfApi, hf_hub_url


PROJECT_ROOT = Path(os.environ.get("H3_PROJECT_ROOT", "/opt/minimax-h3"))
MANIFEST_PATH = PROJECT_ROOT / "config" / "model-manifest.json"
REPO_ID = "Comfy-Org/MiniMax-H3"
REVISION = "main"


def fail(message: str) -> "NoReturn":
    print(f"[h3][error] {message}", file=sys.stderr)
    raise SystemExit(1)


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read manifest: {exc}")


def selected_files(manifest: dict) -> list[dict]:
    files = manifest["files"]
    preset = os.environ.get("MODEL_PRESET", "5090").strip().lower()
    raw_custom = os.environ.get("MODEL_FILES", "").strip()

    by_name = {item["name"]: item for item in files}
    if raw_custom:
        names = [name.strip() for name in raw_custom.split(",") if name.strip()]
        unknown = [name for name in names if name not in by_name]
        if unknown:
            fail("unknown MODEL_FILES: " + ", ".join(unknown))
        return [by_name[name] for name in names]

    if preset == "full":
        return files
    if preset not in manifest["presets"]:
        fail(f"unknown MODEL_PRESET={preset}; use 5090, 6000, full or MODEL_FILES")
    return [by_name[name] for name in manifest["presets"][preset]]


def auth_headers() -> dict[str, str]:
    token = os.environ.get("HF_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def verify_access(token: str) -> None:
    try:
        api = HfApi(token=token or None)
        if token:
            identity = api.whoami()
            username = identity.get("name") or identity.get("email") or "authenticated user"
            print(f"[h3] Hugging Face token accepted for {username}")
        info = api.model_info(REPO_ID, revision=REVISION)
        print(f"[h3] Hugging Face access OK: {info.id}")
    except Exception as exc:  # Hugging Face raises several version-specific errors.
        fail(f"Hugging Face access failed for {REPO_ID}: {exc}")


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "calculating"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def file_sizes(token: str, items: list[dict]) -> dict[str, int]:
    try:
        info = HfApi(token=token or None).model_info(REPO_ID, revision=REVISION, files_metadata=True)
        sizes = {s.rfilename: (s.size or 0) for s in info.siblings}
        missing = [item["path"] for item in items if not sizes.get(item["path"])]
        if missing:
            fail("size metadata missing for: " + ", ".join(missing))
        return sizes
    except Exception as exc:
        fail(f"cannot read model sizes: {exc}")


def probe(item: dict, root: Path, byte_count: int) -> None:
    url = hf_hub_url(REPO_ID, item["path"], revision=REVISION)
    request = Request(url, headers={**auth_headers(), "Range": f"bytes=0-{byte_count - 1}"})
    destination = root / item["destination"] / f".probe-{item['name']}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read(byte_count)
            if not data:
                fail(f"empty probe response for {item['name']}")
            destination.write_bytes(data)
            print(f"[h3][probe] {item['name']}: {len(data)} bytes -> {destination}")
    except (HTTPError, URLError, OSError) as exc:
        fail(f"probe failed for {item['name']}: {exc}")


def download(item: dict, root: Path, index: int, count: int, batch_total: int, batch_done: int, batch_started: float) -> int:
    destination = root / item["destination"] / item["name"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        size = destination.stat().st_size
        print(f"[h3][skip] {item['name']} already exists ({size / (1024**3):.2f} GB)", flush=True)
        return size

    url = hf_hub_url(REPO_ID, item["path"], revision=REVISION)
    request = Request(url, headers=auth_headers())
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            next_report = 0
            started = time.monotonic()
            print(
                f"[h3][download-start] {item['name']}"
                + (f" ({total / (1024**3):.2f} GB)" if total else ""),
                flush=True,
            )
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_report:
                    if total:
                        percent = downloaded / total * 100
                        print(
                            f"[h3][download-progress] {item['name']}: "
                            f"{percent:.1f}% ({downloaded / (1024**3):.2f}/"
                            f"{total / (1024**3):.2f} GB)",
                            flush=True,
                        )
                    else:
                        print(
                            f"[h3][download-progress] {item['name']}: "
                            f"{downloaded / (1024**3):.2f} GB",
                            flush=True,
                        )
                    elapsed = time.monotonic() - batch_started
                    overall = (batch_done + downloaded) / batch_total * 100 if batch_total else 100
                    speed = (batch_done + downloaded) / elapsed if elapsed > 0 else 0
                    eta = (batch_total - batch_done - downloaded) / speed if speed > 0 else 0
                    print(
                        f"[h3][overall] file {index}/{count}, {overall:.1f}% of "
                        f"{batch_total / (1024**3):.2f} GB, speed "
                        f"{speed / (1024**2):.1f} MB/s, elapsed {format_duration(elapsed)}, "
                        f"ETA {format_duration(eta)}",
                        flush=True,
                    )
                    next_report = downloaded + 256 * 1024 * 1024
        temporary.replace(destination)
        print(
            f"[h3][downloaded] {destination} ({downloaded / (1024**3):.2f} GB)",
            flush=True,
        )
        return downloaded
    except (HTTPError, URLError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        fail(f"download failed for {item['name']}: {exc}")


def main() -> None:
    manifest = load_manifest()
    root = Path(os.environ.get("MODEL_ROOT", "/workspace/models"))
    mode = os.environ.get("DOWNLOAD_MODE", "missing").strip().lower()
    if mode not in {"skip", "probe", "missing", "full"}:
        fail("DOWNLOAD_MODE must be skip, probe, missing or full")

    if mode == "skip":
        print("[h3] model download skipped")
        return

    token = os.environ.get("HF_TOKEN", "").strip()
    verify_access(token)
    items = selected_files(manifest)
    sizes = file_sizes(token, items)
    total_size = sum(sizes[item["path"]] for item in items)
    print(f"[h3] selected {len(items)} model file(s), total {total_size / (1024**3):.2f} GB", flush=True)

    if mode == "probe":
        byte_count = max(1024, int(os.environ.get("MODEL_PROBE_BYTES", "1048576")))
        for item in items:
            probe(item, root, byte_count)
        print("[h3] probe complete; no full model was downloaded")
        return

    batch_done = 0
    batch_started = time.monotonic()
    for index, item in enumerate(items, 1):
        batch_done += download(item, root, index, len(items), total_size, batch_done, batch_started)
    elapsed = time.monotonic() - batch_started
    print(f"[h3] model download complete: {len(items)} files, {total_size / (1024**3):.2f} GB, elapsed {format_duration(elapsed)}", flush=True)


if __name__ == "__main__":
    main()
