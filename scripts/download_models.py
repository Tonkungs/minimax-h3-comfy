#!/usr/bin/env python3
"""Download selected Comfy-Org MiniMax H3 files into ComfyUI folders."""

from __future__ import annotations

import errno
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from huggingface_hub import HfApi, hf_hub_url

from h3_control import boot_recipe, recipe_items


PROJECT_ROOT = Path(os.environ.get("H3_PROJECT_ROOT", "/opt/minimax-h3"))
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "config" / "model-manifest.json"
REPO_ID = "Comfy-Org/MiniMax-H3"
REVISION = "main"


def fail(message: str) -> "NoReturn":
    print(f"[h3][error] {message}", file=sys.stderr)
    exc = SystemExit(1)
    exc.h3_message = message
    raise exc


def load_manifest() -> dict:
    inline = os.environ.get("MODEL_MANIFEST_JSON", "").strip()
    remote_url = os.environ.get("MODEL_MANIFEST_URL", "").strip()
    manifest_path = Path(os.environ.get("MODEL_MANIFEST", str(DEFAULT_MANIFEST_PATH)))
    try:
        if inline:
            manifest = json.loads(inline)
        elif remote_url:
            if urlparse(remote_url).scheme not in {"http", "https"}:
                fail("MODEL_MANIFEST_URL must use http or https")
            request = Request(remote_url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=60) as response:
                manifest = json.loads(response.read().decode("utf-8"))
        else:
            manifest = json.loads(manifest_path.read_text())
    except (OSError, HTTPError, URLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        fail(f"cannot read manifest: {exc}")
    if isinstance(manifest, list):
        normalized = []
        for entry in manifest:
            if not isinstance(entry, dict) or not entry.get("url") or not entry.get("destination"):
                fail("override manifest entries require url and destination")
            url = str(entry["url"])
            name = str(entry.get("name") or unquote(urlparse(url).path.rstrip("/").split("/")[-1]))
            if not name:
                fail(f"cannot derive filename from URL: {url}")
            if not isinstance(entry.get("download", True), bool):
                fail(f"download must be boolean for {name}")
            normalized.append({**entry, "name": name, "path": url, "direct_url": True})
        return {"files": normalized, "presets": {"override": [item["name"] for item in normalized]}}
    if not isinstance(manifest, dict) or "files" not in manifest:
        fail("manifest must be an object with files or a JSON array of url entries")
    for item in manifest["files"]:
        if not isinstance(item.get("download", True), bool):
            fail(f"download must be boolean for {item.get('name', '<unnamed>')}")
    return manifest


def selected_files(manifest: dict) -> list[dict]:
    files = [item for item in manifest["files"] if item.get("download", True)]
    preset = os.environ.get("MODEL_PRESET", "").strip().lower()
    if not preset:
        preset = "override" if "override" in manifest["presets"] else "5090"
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
    if any(item.get("direct_url") for item in items):
        sizes = {}
        for item in items:
            request = Request(item["url"], method="HEAD", headers=auth_headers())
            try:
                with urlopen(request, timeout=60) as response:
                    sizes[item["path"]] = int(response.headers.get("Content-Length", "0") or 0)
            except (HTTPError, URLError, OSError) as exc:
                fail(f"cannot read size for {item['name']}: {exc}")
        return sizes
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
    url = item["url"] if item.get("direct_url") else hf_hub_url(REPO_ID, item["path"], revision=REVISION)
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


def download(item: dict, root: Path, index: int, count: int, batch_total: int, batch_done: int, batch_started: float, expected_size: int) -> int:
    destination = root / item["destination"] / item["name"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == expected_size:
        size = destination.stat().st_size
        print(f"[h3][skip-verified] {item['name']} already complete ({size / (1024**3):.2f} GB)", flush=True)
        return size
    if destination.exists() and destination.stat().st_size > 0 and not item.get("direct_url"):
        size = destination.stat().st_size
        print(f"[h3][skip] {item['name']} already exists ({size / (1024**3):.2f} GB)", flush=True)
        return size

    url = item["url"] if item.get("direct_url") else hf_hub_url(REPO_ID, item["path"], revision=REVISION)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        total = expected_size
        if total <= 0:
            fail(f"invalid expected size for {item['name']}: {total}")

        def read_range(start: int, end: int) -> bytes:
            request = Request(url, headers={**auth_headers(), "Range": f"bytes={start}-{end}"})
            with urlopen(request, timeout=120) as response:
                content_range = response.headers.get("Content-Range", "")
                expected_range = f"bytes {start}-{end}/{total}"
                if response.status != 206 or content_range != expected_range:
                    raise OSError(
                        f"unexpected range response for {item['name']}: "
                        f"status={response.status}, content-range={content_range!r}"
                    )
                data = response.read(end - start + 1)
                if len(data) != end - start + 1:
                    raise OSError(
                        f"short range for {item['name']}: got {len(data)} bytes, "
                        f"expected {end - start + 1}"
                    )
                return data

        def read_retry(start: int, end: int) -> bytes:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    return read_range(start, end)
                except (HTTPError, URLError, OSError, TimeoutError) as exc:
                    last_error = exc
                    print(
                        f"[h3][range-retry] {item['name']} bytes={start}-{end} "
                        f"attempt={attempt}/3 error={type(exc).__name__}",
                        flush=True,
                    )
                    if attempt < 3:
                        time.sleep(attempt * 2)
            raise OSError(f"range download failed for {item['name']}: {last_error}")

        # Direct-URL items only (manifest items with a partial file are skipped above): resume
        # only when the existing .part prefix is demonstrably from this URL.
        existing_size = temporary.stat().st_size if temporary.exists() else 0
        # A completed-looking file from an older truncated transfer is not trusted.
        if destination.exists() and destination.stat().st_size < total:
            if temporary.exists():
                temporary.unlink()
            destination.replace(temporary)
            existing_size = temporary.stat().st_size
        elif destination.exists() and destination.stat().st_size != total:
            destination.unlink()
        if existing_size > total:
            temporary.unlink(missing_ok=True)
            existing_size = 0
        if existing_size:
            prefix_end = min(1023, total - 1)
            with temporary.open("rb") as prefix:
                same = prefix.read(prefix_end + 1) == read_retry(0, prefix_end)
            if not same:
                print(f"[h3][resume-reset] {item['name']} prefix did not match source", flush=True)
                temporary.unlink(missing_ok=True)
                existing_size = 0

        downloaded = existing_size
        print(
            f"[h3][download-start] {item['name']} ({total / (1024**3):.2f} GB)"
            + (f" resume={downloaded / (1024**3):.2f} GB" if downloaded else ""),
            flush=True,
        )
        chunk_size = 256 * 1024 * 1024
        with temporary.open("ab") as output:
            while downloaded < total:
                end = min(downloaded + chunk_size, total) - 1
                data = read_retry(downloaded, end)
                output.write(data)
                output.flush()
                downloaded += len(data)
                elapsed = time.monotonic() - batch_started
                overall = (batch_done + downloaded) / batch_total * 100 if batch_total else 100
                speed = (batch_done + downloaded) / elapsed if elapsed > 0 else 0
                eta = (batch_total - batch_done - downloaded) / speed if speed > 0 else 0
                print(
                    f"[h3][progress] file {index}/{count} | {item['name']} | "
                    f"current {downloaded / total * 100:.1f}% ({downloaded / (1024**3):.2f}/"
                    f"{total / (1024**3):.2f} GB) | overall {overall:.1f}% "
                    f"({(batch_done + downloaded) / (1024**3):.2f}/"
                    f"{batch_total / (1024**3):.2f} GB) | speed "
                    f"{speed / (1024**2):.1f} MB/s | elapsed {format_duration(elapsed)} | "
                    f"ETA {format_duration(eta)}",
                    flush=True,
                )
        actual_size = temporary.stat().st_size
        if actual_size != total:
            raise OSError(f"incomplete model {item['name']}: got {actual_size} bytes, expected {total}")
        temporary.replace(destination)
        print(
            f"[h3][downloaded] {destination} ({actual_size / (1024**3):.2f} GB)",
            flush=True,
        )
        return actual_size
    except (HTTPError, URLError, OSError) as exc:
        # Keep the .part for resume unless it is oversized or the disk is full.
        if getattr(exc, "errno", None) == errno.ENOSPC or (temporary.exists() and temporary.stat().st_size > expected_size):
            temporary.unlink(missing_ok=True)
        fail(f"download failed for {item['name']}: {exc}")


def fetch_extra(extra: list[dict], root: Path, mode: str = "missing") -> None:
    """Download recipe/upscaler files (direct URLs outside the Comfy-Org manifest)."""
    items = [{**item, "path": item["url"], "direct_url": True} for item in extra]
    sizes = file_sizes("", items)
    total_size = sum(sizes[item["path"]] for item in items)
    print(f"[h3] selected {len(items)} recipe file(s), total {total_size / (1024**3):.2f} GB", flush=True)
    if mode == "probe":
        for item in items:
            probe(item, root, max(1024, int(os.environ.get("MODEL_PROBE_BYTES", "1048576"))))
        return
    done, started = 0, time.monotonic()
    for index, item in enumerate(items, 1):
        done += download(item, root, index, len(items), total_size, done, started, sizes[item["path"]])


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
    items = selected_files(manifest)
    if not any(item.get("direct_url") for item in items):
        verify_access(token)
    sizes = file_sizes(token, items)
    total_size = sum(sizes[item["path"]] for item in items)
    print(f"[h3] selected {len(items)} model file(s), total {total_size / (1024**3):.2f} GB", flush=True)

    if mode == "probe":
        byte_count = max(1024, int(os.environ.get("MODEL_PROBE_BYTES", "1048576")))
        for item in items:
            probe(item, root, byte_count)
        fetch_extra(recipe_items(boot_recipe()[0]), root, mode)
        print("[h3] probe complete; no full model was downloaded")
        return

    batch_done = 0
    batch_started = time.monotonic()
    for index, item in enumerate(items, 1):
        batch_done += download(item, root, index, len(items), total_size, batch_done, batch_started, sizes[item["path"]])
    recipe, fallback = boot_recipe()
    print(f"[h3] video recipe={recipe}" + (f" (H3_RECIPE={fallback!r} unknown, fell back to base)" if fallback else ""), flush=True)
    fetch_extra(recipe_items(recipe), root)
    elapsed = time.monotonic() - batch_started
    print(f"[h3] model download complete: {len(items)} files, {total_size / (1024**3):.2f} GB, elapsed {format_duration(elapsed)}", flush=True)


if __name__ == "__main__":
    main()
