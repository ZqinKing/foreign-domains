#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build pure-foreign domain block lists for dnsmasq.

Scheme B:
  ban = (geolocation-!cn | union(*@!cn)) - union(*@cn)

Source:
  v2fly/domain-list-community dlc.dat
Tool:
  snowie2000/geoview
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / ".cache"
DEFAULT_DIST = ROOT / "dist"

DLC_URL = "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat"
GEOVIEW_RELEASE_BASE = "https://github.com/snowie2000/geoview/releases/download"
DEFAULT_GEOVIEW_VERSION = "0.2.6"
BATCH_SIZE = 80

FORMULA = "ban = (geolocation-!cn | union(*@!cn)) - union(*@cn)"


def log(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log(f"download: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "pure-foreign-domains-builder"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(dest)
    log(f"saved: {dest} ({dest.stat().st_size} bytes)")


def geoview_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system.startswith("linux"):
        os_name = "linux"
    elif system == "darwin":
        # geoview release assets may not always include darwin; fall back handled by caller
        os_name = "darwin"
    elif system in {"windows", "cygwin", "msys"}:
        os_name = "windows"
    else:
        raise RuntimeError(f"unsupported OS: {system}")

    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    elif machine in {"i386", "i686", "x86"}:
        arch = "i386"
    else:
        # common CI is amd64
        arch = "amd64"
        log(f"warning: unknown arch {machine}, defaulting to amd64")

    if os_name == "windows":
        return f"geoview-windows-{arch}.exe"
    return f"geoview-{os_name}-{arch}"


def ensure_geoview(cache: Path, version: str, explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit

    asset = geoview_asset_name()
    path = cache / "bin" / asset
    if path.exists() and path.stat().st_size > 0:
        return path

    url = f"{GEOVIEW_RELEASE_BASE}/{version}/{asset}"
    try:
        download(url, path)
    except Exception as exc:
        # fallback to linux amd64 naming notes for local windows already covered
        raise RuntimeError(f"failed to download geoview asset {asset}: {exc}") from exc

    if os.name != "nt":
        path.chmod(path.stat().st_mode | 0o111)
    return path


def ensure_geosite(cache: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    path = cache / "data" / "geosite.dat"
    download(DLC_URL, path)
    return path


def run_geoview(geoview: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [str(geoview), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def extract_list(geoview: Path, geosite: Path, spec: str, output: Path) -> list[str]:
    if output.exists():
        output.unlink()
    proc = run_geoview(
        geoview,
        [
            "-input",
            str(geosite),
            "-type",
            "geosite",
            "-action",
            "extract",
            "-list",
            spec,
            "-output",
            str(output),
            "-strict=false",
        ],
    )
    if proc.returncode != 0 and not output.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"geoview extract failed for {spec!r}: {err}")
    if not output.exists():
        return []
    domains: list[str] = []
    for line in output.read_text(encoding="utf-8", errors="replace").splitlines():
        d = line.strip().lower()
        if d and not d.startswith("#") and "." in d:
            domains.append(d)
    return sorted(set(domains))


def list_codes(geoview: Path, geosite: Path) -> list[str]:
    proc = run_geoview(
        geoview,
        [
            "-input",
            str(geosite),
            "-type",
            "geosite",
            "-action",
            "extract",
        ],
    )
    codes: list[str] = []
    for line in (proc.stdout or "").splitlines():
        s = line.strip()
        if not s or s.lower().startswith("available codes"):
            continue
        codes.append(s)
    if not codes:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"failed to list geosite codes: {err}")
    return codes


def extract_attr_union(
    geoview: Path,
    geosite: Path,
    codes: list[str],
    attr: str,
    tmp: Path,
    prefix: str,
) -> list[str]:
    collected: set[str] = set()
    specs = [f"{c}@{attr}" for c in codes]
    total = (len(specs) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(specs), BATCH_SIZE):
        batch = specs[i : i + BATCH_SIZE]
        idx = i // BATCH_SIZE
        out = tmp / f"{prefix}-batch-{idx:04d}.txt"
        got = extract_list(geoview, geosite, ",".join(batch), out)
        before = len(collected)
        collected.update(got)
        log(
            f"  {prefix} batch {idx + 1}/{total}: +{len(collected) - before} "
            f"(unique total {len(collected)})"
        )
    return sorted(collected)


def is_exempt(domain: str, exempt_set: set[str], exempt_list: list[str]) -> bool:
    if domain in exempt_set:
        return True
    for e in exempt_list:
        if domain.endswith("." + e):
            return True
    return False


def would_block_exempt(domain: str, exempt_set: set[str], exempt_list: list[str]) -> bool:
    if domain in exempt_set:
        return True
    prefix = domain + "."
    for e in exempt_list:
        if e == domain or e.startswith(prefix):
            return True
    return False


def minimize_suffixes(domains: list[str]) -> list[str]:
    kept_set: set[str] = set()
    kept: list[str] = []
    for d in sorted(set(domains), key=lambda x: (x.count("."), len(x), x)):
        covered = False
        parts = d.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in kept_set:
                covered = True
                break
        if not covered:
            kept.append(d)
            kept_set.add(d)
    return sorted(kept)


def blocked_by_suffix(query: str, ban_set: set[str]) -> bool:
    if query in ban_set:
        return True
    parts = query.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[i:]) in ban_set:
            return True
    return False


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def build(args: argparse.Namespace) -> int:
    cache = Path(args.cache).resolve()
    dist = Path(args.dist).resolve()
    tmp = cache / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)

    geoview = ensure_geoview(
        cache,
        args.geoview_version,
        Path(args.geoview).resolve() if args.geoview else None,
    )
    geosite = ensure_geosite(
        cache,
        Path(args.geosite).resolve() if args.geosite else None,
    )

    log(f"geoview: {geoview}")
    log(f"geosite: {geosite}")

    log("==> list codes")
    codes = list_codes(geoview, geosite)
    write_text(tmp / "all-codes.txt", "\n".join(codes) + "\n")
    log(f"codes: {len(codes)}")

    log("==> extract geolocation-!cn")
    foreign = extract_list(geoview, geosite, "geolocation-!cn", tmp / "foreign.txt")
    log(f"foreign: {len(foreign)}")

    log("==> extract all *@!cn")
    not_cn_attr = extract_attr_union(geoview, geosite, codes, "!cn", tmp, "notcn")
    write_text(tmp / "all-notcn-attr.txt", "\n".join(not_cn_attr) + "\n")
    log(f"@!cn: {len(not_cn_attr)}")

    log("==> extract all *@cn")
    cn_attr = extract_attr_union(geoview, geosite, codes, "cn", tmp, "cn")
    write_text(tmp / "all-cn-attr.txt", "\n".join(cn_attr) + "\n")
    log(f"@cn: {len(cn_attr)}")

    candidates = sorted(set(foreign) | set(not_cn_attr))
    only_from_notcn = sorted(set(not_cn_attr) - set(foreign))
    log(f"candidates: {len(candidates)} (only @!cn added: {len(only_from_notcn)})")

    exempt_set = set(cn_attr)
    exempt_list = list(cn_attr)

    raw_diff: list[str] = []
    dnsmasq_safe: list[str] = []
    removed_exact_or_sub = 0
    removed_parent_of_exempt = 0

    for d in candidates:
        if is_exempt(d, exempt_set, exempt_list):
            removed_exact_or_sub += 1
            continue
        raw_diff.append(d)
        if would_block_exempt(d, exempt_set, exempt_list):
            removed_parent_of_exempt += 1
            continue
        dnsmasq_safe.append(d)

    raw_diff = sorted(set(raw_diff))
    dnsmasq_safe = sorted(set(dnsmasq_safe))
    domains = minimize_suffixes(dnsmasq_safe)
    ban_set = set(domains)

    deprecated = extract_list(
        geoview, geosite, "geolocation-cn@!cn", tmp / "geolocation-cn-notcn.txt"
    )

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    validation = {
        "youtube.com_in_ban": blocked_by_suffix("youtube.com", ban_set),
        "www.youtube.com_blocked_by_suffix": blocked_by_suffix("www.youtube.com", ban_set),
        "www.apple.com_in_ban": "www.apple.com" in ban_set,
        "apple.com_in_ban": "apple.com" in ban_set,
        "www.apple.com_in_cn": "www.apple.com" in exempt_set,
        "bilibili.tv_in_ban": blocked_by_suffix("bilibili.tv", ban_set),
        "aliexpress.ru_in_ban": blocked_by_suffix("aliexpress.ru", ban_set),
        "geolocation-cn@!cn_deprecated_empty": len(deprecated) == 0,
    }

    meta = {
        "generated_at": generated_at,
        "scheme": "B",
        "formula": FORMULA,
        "source": {
            "geosite_project": "v2fly/domain-list-community",
            "geosite_file": "dlc.dat",
            "geosite_size_bytes": geosite.stat().st_size,
            "tool": f"snowie2000/geoview@{args.geoview_version}",
            "notes": [
                "Rules with @!cn have been cast out from cn lists.",
                "geosite:geolocation-cn@!cn is no longer available.",
                "See domain-list-community #390, #3119, #3198.",
            ],
        },
        "counts": {
            "codes": len(codes),
            "foreign_geolocation_not_cn": len(foreign),
            "attr_not_cn": len(not_cn_attr),
            "attr_cn": len(cn_attr),
            "candidates_foreign_union_notcn": len(candidates),
            "added_only_by_notcn": len(only_from_notcn),
            "raw_diff": len(raw_diff),
            "dnsmasq_safe": len(dnsmasq_safe),
            "domains": len(domains),
            "removed_exact_or_subdomain_of_cn": removed_exact_or_sub,
            "removed_parent_of_exempt": removed_parent_of_exempt,
            "deprecated_geolocation_cn_notcn": len(deprecated),
        },
        "validation": validation,
        "artifacts": {
            "pure-foreign.txt": "plain domain list, one domain per line",
            "pure-foreign.conf": "dnsmasq conf, NXDOMAIN via address=/domain/",
            "pure-foreign-0.0.0.0.conf": "dnsmasq conf, address=/domain/0.0.0.0",
            "build-meta.json": "generation metadata and validation",
        },
    }

    # plain domain list
    write_text(dist / "pure-foreign.txt", "\n".join(domains) + "\n")

    # dnsmasq NXDOMAIN
    conf_lines = [
        "# pure-foreign domains for dnsmasq",
        f"# generated_at: {generated_at}",
        f"# formula: {FORMULA}",
        f"# count: {len(domains)}",
        "# block mode: NXDOMAIN (address=/domain/)",
        "# project: pure-foreign-domains",
        "",
    ]
    conf_lines.extend(f"address=/{d}/" for d in domains)
    write_text(dist / "pure-foreign.conf", "\n".join(conf_lines) + "\n")

    # dnsmasq 0.0.0.0
    conf0_lines = [
        "# pure-foreign domains for dnsmasq",
        f"# generated_at: {generated_at}",
        f"# formula: {FORMULA}",
        f"# count: {len(domains)}",
        "# block mode: 0.0.0.0",
        "# project: pure-foreign-domains",
        "",
    ]
    conf0_lines.extend(f"address=/{d}/0.0.0.0" for d in domains)
    write_text(dist / "pure-foreign-0.0.0.0.conf", "\n".join(conf0_lines) + "\n")

    write_text(dist / "build-meta.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    # optional richer json for debugging/consumers
    if args.with_json:
        payload = {
            "name": "pure-foreign",
            "scheme": "B",
            "formula": FORMULA,
            "count": len(domains),
            "domains": domains,
            "meta": meta,
        }
        write_text(dist / "pure-foreign.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    # fail CI if critical validation fails
    required_ok = (
        validation["youtube.com_in_ban"]
        and validation["www.youtube.com_blocked_by_suffix"]
        and not validation["www.apple.com_in_ban"]
        and validation["bilibili.tv_in_ban"]
        and validation["geolocation-cn@!cn_deprecated_empty"]
    )
    log("==> validation")
    log(json.dumps(validation, ensure_ascii=True, indent=2))
    log(f"==> done, domains={len(domains)}, dist={dist}")

    if not required_ok:
        log("ERROR: validation failed")
        return 2
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build pure-foreign domain lists")
    p.add_argument("--cache", default=str(DEFAULT_CACHE), help="cache directory")
    p.add_argument("--dist", default=str(DEFAULT_DIST), help="output directory")
    p.add_argument("--geosite", default="", help="path to existing geosite/dlc.dat")
    p.add_argument("--geoview", default="", help="path to existing geoview binary")
    p.add_argument(
        "--geoview-version",
        default=os.environ.get("GEOVIEW_VERSION", DEFAULT_GEOVIEW_VERSION),
        help="geoview release tag",
    )
    p.add_argument(
        "--with-json",
        action="store_true",
        help="also write pure-foreign.json",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.geosite:
        args.geosite = None
    if not args.geoview:
        args.geoview = None
    try:
        return build(args)
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
