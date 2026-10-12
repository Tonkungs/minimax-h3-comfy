#!/usr/bin/env python3
"""Fetch the official Comfy-Org H3 workflows into persistent workspace storage."""

from __future__ import annotations

import json
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


def set_qwen_cpu_default(workflow: dict) -> bool:
    changed = False
    subgraphs = workflow.get("definitions", {}).get("subgraphs", [])
    node_lists = [workflow.get("nodes", [])] + [sg.get("nodes", []) for sg in subgraphs if isinstance(sg, dict)]
    for node in (n for nodes in node_lists for n in nodes):
        if node.get("type") != "CLIPLoader":
            continue
        named = node.get("widgets_values_named")
        if isinstance(named, dict) and named.get("device") != "cpu":
            named["device"] = "cpu"
            changed = True
        values = node.get("widgets_values")
        if isinstance(values, list) and len(values) >= 3 and values[2] != "cpu":
            values[2] = "cpu"
            changed = True
    return changed


def patch_best_effort(workflow: object, name: str = "") -> bool:
    if not isinstance(workflow, dict):
        print(f"[h3][workflow][warning] {name} root is not an object; skipping CLIPLoader patch")
        return False
    try:
        return set_qwen_cpu_default(workflow)
    except (AttributeError, TypeError) as exc:
        print(f"[h3][workflow][warning] CLIPLoader patch skipped for {name}: {exc}")
        return False


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, url in WORKFLOWS.items():
        destination = ROOT / name
        workflow = None
        if is_real_workflow(destination):
            try:
                workflow = json.loads(destination.read_text(encoding="utf-8"))
                message = "already present"
            except (OSError, ValueError) as exc:
                print(f"[h3][workflow][warning] unreadable existing {name}, refetching: {exc}")
        if workflow is None:
            try:
                with urlopen(url, timeout=60) as response:
                    workflow = json.loads(response.read().decode("utf-8"))
                message = "fetched"
            except Exception as exc:
                print(f"[h3][workflow][warning] could not fetch {name}: {exc}")
                continue

        changed = patch_best_effort(workflow, name)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
            cpu_note = "; CLIPLoader device=cpu" if changed else ""
            print(f"[h3][workflow] {message}: {destination}{cpu_note}")
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            print(f"[h3][workflow][warning] could not save {name}: {exc}")


if __name__ == "__main__":
    main()
