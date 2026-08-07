#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror the upstream GFW blocklist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from http.client import HTTPResponse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
DEFAULT_GFW_URL = "https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/gfw.txt"
GFW_FILE = "gfw.txt"
META_FILE = "gfw-meta.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str) -> bytes:
    log(f"download: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "foreign-domains-builder"})
    with cast(HTTPResponse, urllib.request.urlopen(req, timeout=120)) as resp:
        raw = resp.read()
    log(f"downloaded: {len(raw)} bytes")
    return raw


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(content, encoding="utf-8", newline="\n")


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)


def build(args: argparse.Namespace) -> int:
    dist = Path(cast(str, args.dist)).resolve()
    source_url = cast(str, args.source_url)
    dist.mkdir(parents=True, exist_ok=True)

    raw = fetch(source_url)
    text = raw.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise RuntimeError("gfw list is empty")

    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first.startswith("<"):
        raise RuntimeError("unexpected HTML response from gfw source")

    sha256 = hashlib.sha256(raw).hexdigest()
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    write_bytes(dist / GFW_FILE, raw)

    meta = {
        "generated_at": generated_at,
        "source": {
            "url": source_url,
            "sha256": sha256,
            "size_bytes": len(raw),
            "line_count": len(text.splitlines()),
        },
        "count": len(lines),
        "artifacts": {
            GFW_FILE: "上游 gfw.txt 的镜像",
            META_FILE: "构建时间、数量和校验结果",
        },
    }
    write_text(dist / META_FILE, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    log("==> done")
    log(json.dumps({"count": len(lines), "generated_at": generated_at}, ensure_ascii=True))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mirror the upstream GFW list")
    _ = p.add_argument("--dist", default=str(DEFAULT_DIST), help="output directory")
    _ = p.add_argument(
        "--source-url",
        default=os.environ.get("GFW_SOURCE_URL", DEFAULT_GFW_URL),
        help="upstream gfw.txt URL",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return build(args)
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
