#!/usr/bin/env python3
"""Download selected Comfy-Org MiniMax H3 files into ComfyUI folders."""

from __future__ import annotations

import json
import os
import sys
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


def download(item: dict, root: Path) -> None:
    destination = root / item["destination"] / item["name"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        print(f"[h3][skip] {item['name']} already exists")
        return

    url = hf_hub_url(REPO_ID, item["path"], revision=REVISION)
    request = Request(url, headers=auth_headers())
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
        temporary.replace(destination)
        print(f"[h3][downloaded] {destination}")
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
    print(f"[h3] selected {len(items)} model file(s)")

    if mode == "probe":
        byte_count = max(1024, int(os.environ.get("MODEL_PROBE_BYTES", "1048576")))
        for item in items:
            probe(item, root, byte_count)
        print("[h3] probe complete; no full model was downloaded")
        return

    for item in items:
        download(item, root)
    print("[h3] model download complete")


if __name__ == "__main__":
    main()
