#!/usr/bin/env python3
"""Fetch the official Comfy-Org H3 workflows into persistent workspace storage."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(os.environ.get("WORKFLOW_ROOT", "/workspace/workflows"))
BASE = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates"
WORKFLOWS = {
    "video_minimax_h3_t2v.json": f"{BASE}/video_minimax_h3_t2v.json",
    "video_minimax_h3_i2v.json": f"{BASE}/video_minimax_h3_i2v.json",
    "video_minimax_h3_r2v.json": f"{BASE}/video_minimax_h3_r2v.json",
}


def is_real_workflow(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return '"nodes"' in text and '"links"' in text


ROOT.mkdir(parents=True, exist_ok=True)
for name, url in WORKFLOWS.items():
    destination = ROOT / name
    if is_real_workflow(destination):
        print(f"[h3][workflow] already present: {destination}")
        continue
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(url, timeout=60) as response:
            temporary.write_bytes(response.read())
        temporary.replace(destination)
        print(f"[h3][workflow] fetched: {destination}")
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        print(f"[h3][workflow][warning] could not fetch {name}: {exc}")
