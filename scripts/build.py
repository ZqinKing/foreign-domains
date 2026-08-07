#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build foreign domain block lists for dnsmasq.

Rule:
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
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / ".cache"
DEFAULT_DIST = ROOT / "dist"

DLC_URL = "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat"
GEOVIEW_RELEASE_BASE = "https://github.com/snowie2000/geoview/releases/download"
DEFAULT_GEOVIEW_VERSION = "0.2.6"
BATCH_SIZE = 80

FORMULA = "ban = (geolocation-!cn | union(*@!cn)) - union(*@cn) - geosite:category-dev"
EXCLUDED_LISTS = ("category-dev",)
DOMAIN_LIST_FILE = "foreign-domains.txt"
DNSMASQ_FILE = "foreign-domains-dnsmasq.conf"
DNSMASQ_ZERO_FILE = "foreign-domains-dnsmasq-0.0.0.0.conf"
JSON_FILE = "foreign-domains.json"
META_FILE = "foreign-domains-meta.json"
REGEXP_REPORT_FILE = "foreign-domains-regexp-report.json"
REGEXP_EXPANSION_LIMIT = 50
REGEXP_BLOCKED_CLASSES = ("[0-9]", "[1-9]", "[a-z]")
GEOSITE_DOMAIN_REGEX = 1


def log(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log(f"download: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "foreign-domains-builder"})
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


def extract_list(
    geoview: Path, geosite: Path, spec: str, output: Path, require_dot: bool = True
) -> list[str]:
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
        if d and not d.startswith("#") and (not require_dot or "." in d):
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


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")


def read_varint_from_slice(data: bytes) -> tuple[int, int]:
    return read_varint(data, 0)


def parse_protobuf_fields(data: bytes) -> list[tuple[int, int, bytes | int]]:
    fields: list[tuple[int, int, bytes | int]] = []
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, pos = read_varint(data, pos)
            fields.append((field, wire, value))
        elif wire == 2:
            size, pos = read_varint(data, pos)
            value = data[pos : pos + size]
            if len(value) != size:
                raise ValueError("truncated length-delimited field")
            pos += size
            fields.append((field, wire, value))
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire}")
    return fields


def parse_attribute(data: bytes) -> str:
    key = ""
    for field, wire, value in parse_protobuf_fields(data):
        if field == 1 and wire == 2 and isinstance(value, bytes):
            key = value.decode("utf-8", errors="replace")
    return key


def parse_domain(data: bytes) -> tuple[int, str, set[str]]:
    domain_type = 0
    domain_value = ""
    attrs: set[str] = set()
    for field, wire, value in parse_protobuf_fields(data):
        if field == 1 and wire == 0 and isinstance(value, int):
            domain_type = int(value)
        elif field == 2 and wire == 2 and isinstance(value, bytes):
            domain_value = value.decode("utf-8", errors="replace")
        elif field == 3 and wire == 2 and isinstance(value, bytes):
            attrs.add(parse_attribute(value))
    return domain_type, domain_value, attrs


def parse_geosite_dat(path: Path) -> list[tuple[str, list[tuple[int, str, set[str]]]]]:
    data = path.read_bytes()
    lists: list[tuple[str, list[tuple[int, str, set[str]]]]] = []
    while len(data) >= 2:
        body_len, body_len_size = read_varint_from_slice(data[1:])
        if body_len == 0 and body_len_size == 0:
            break
        head_len = 1 + body_len_size
        if len(data) < head_len + body_len:
            break
        entry = data[head_len : head_len + body_len]
        if len(entry) < 3:
            data = data[head_len + body_len :]
            continue
        code_len = entry[1]
        if code_len == 0 or len(entry) < 2 + code_len:
            data = data[head_len + body_len :]
            continue
        code = entry[2 : 2 + code_len].decode("utf-8", errors="replace")
        proto_bytes = entry[2 + code_len :]
        domains: list[tuple[int, str, set[str]]] = []
        for sub_field, sub_wire, sub_value in parse_protobuf_fields(proto_bytes):
            if sub_field == 2 and sub_wire == 2 and isinstance(sub_value, bytes):
                domains.append(parse_domain(sub_value))
        lists.append((code, domains))
        data = data[head_len + body_len :]
    return lists


def contains_blocked_class(pattern: str) -> bool:
    return any(fragment in pattern for fragment in REGEXP_BLOCKED_CLASSES)


def pattern_to_literal_suffixes(pattern: str) -> list[str]:
    if not pattern.startswith(r"(^|\.)") or not pattern.endswith("$"):
        raise ValueError("no_subdomain_boundary")
    tail = pattern[len(r"(^|\.)") : -1]

    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import sre_constants
            import sre_parse
    except Exception as exc:  # pragma: no cover - import failure is fatal in build
        raise RuntimeError(f"failed to load regex parser: {exc}") from exc

    def expand_subpattern(subpattern) -> list[str]:
        parts: list[list[str]] = []
        for token, value in subpattern:
            parts.append(expand_token(token, value))
        out = [""]
        for opts in parts:
            if len(out) * len(opts) > REGEXP_EXPANSION_LIMIT:
                raise OverflowError("finite_expansion_exceeds_limit")
            out = [a + b for a in out for b in opts]
        return out

    def expand_char_class(items) -> list[str]:
        chars: list[str] = []
        for op, value in items:
            op_name = getattr(op, "name", str(op))
            if op_name == "NEGATE":
                raise ValueError("unsupported_negated_class")
            if op_name == "LITERAL":
                chars.append(chr(value))
            elif op_name == "RANGE":
                start, end = value
                chars.extend(chr(code) for code in range(start, end + 1))
            else:
                raise ValueError(f"unsupported_char_class:{op_name}")
            if len(chars) > REGEXP_EXPANSION_LIMIT:
                raise OverflowError("finite_expansion_exceeds_limit")
        return sorted(set(chars))

    def expand_token(token, value) -> list[str]:
        token_name = getattr(token, "name", str(token))
        if token_name == "LITERAL":
            return [chr(value)]
        if token_name == "SUBPATTERN":
            return expand_subpattern(value[-1])
        if token_name == "BRANCH":
            out: list[str] = []
            for branch in value[1]:
                out.extend(expand_subpattern(branch))
                if len(out) > REGEXP_EXPANSION_LIMIT:
                    raise OverflowError("finite_expansion_exceeds_limit")
            return sorted(set(out))
        if token_name in {"MAX_REPEAT", "MIN_REPEAT"}:
            min_count, max_count, child = value
            if max_count == sre_constants.MAXREPEAT:
                raise ValueError("unbounded_repeat")
            child_opts = expand_subpattern(child)
            out: list[str] = []
            for count in range(min_count, max_count + 1):
                if count == 0:
                    out.append("")
                    continue
                if len(child_opts) ** count > REGEXP_EXPANSION_LIMIT:
                    raise OverflowError("finite_expansion_exceeds_limit")
                for combo in product(child_opts, repeat=count):
                    out.append("".join(combo))
                    if len(out) > REGEXP_EXPANSION_LIMIT:
                        raise OverflowError("finite_expansion_exceeds_limit")
            return sorted(set(out))
        if token_name == "IN":
            return expand_char_class(value)
        if token_name == "AT":
            return [""]
        raise ValueError(f"unsupported_token:{token_name}")

    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            parsed = sre_parse.parse(tail)
        suffixes = sorted(set(expand_subpattern(parsed)))
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    if not suffixes:
        raise ValueError("empty_expansion")
    for suffix in suffixes:
        if not suffix or "." not in suffix:
            raise ValueError("invalid_domain_expansion")
        if not re.fullmatch(r"[a-z0-9.-]+", suffix):
            raise ValueError("invalid_domain_expansion")
        labels = suffix.split(".")
        if any(
            not (0 < len(label) <= 63 and not label.startswith("-") and not label.endswith("-"))
            for label in labels
        ):
            raise ValueError("invalid_domain_expansion")
    return suffixes


def extract_regex_candidates(geosite: Path) -> tuple[list[str], dict[str, object]]:
    selected: list[str] = []
    selected_sources: list[str] = []
    safe_expanded: set[str] = set()
    report_rows: list[dict[str, object]] = []

    for list_name, domains in parse_geosite_dat(geosite):
        list_name_key = list_name.upper()
        if list_name_key == "CATEGORY-DEV":
            continue
        for domain_type, value, attrs in domains:
            if domain_type != GEOSITE_DOMAIN_REGEX:
                continue
            attr_keys = {a.lower() for a in attrs if a}
            if list_name_key != "GEOLOCATION-!CN" and "!cn" not in attr_keys:
                continue
            if "cn" in attr_keys:
                continue

            rule = f"regexp:{value}"
            selected.append(rule)
            selected_sources.append(list_name)

            blocked = contains_blocked_class(value)
            status = "safe"
            expansions: list[str] = []
            reason = ""
            if blocked:
                status = "blocked_class"
                reason = "contains_blocked_character_class"
            else:
                try:
                    expansions = pattern_to_literal_suffixes(value)
                    if len(expansions) > REGEXP_EXPANSION_LIMIT:
                        raise OverflowError("finite_expansion_exceeds_limit")
                    safe_expanded.update(expansions)
                except Exception as exc:
                    status = "unsafe"
                    reason = str(exc)

            report_rows.append(
                {
                    "rule": rule,
                    "source_list": list_name,
                    "status": status,
                    "reason": reason,
                    "expansion_count": len(expansions),
                    "expansions": expansions,
                }
            )

    meta = {
        "selected_regexp_rules": len(selected),
        "selected_sources": sorted(set(selected_sources)),
        "safe_expanded_regexp_rules": sum(1 for row in report_rows if row["status"] == "safe"),
        "rejected_regexp_rules": sum(1 for row in report_rows if row["status"] != "safe"),
        "expanded_domains": len(safe_expanded),
    }
    return sorted(safe_expanded), {"meta": meta, "rows": report_rows}


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

    excluded_domains: set[str] = set()
    excluded_counts: dict[str, int] = {}
    for code in EXCLUDED_LISTS:
        log(f"==> extract excluded list {code}")
        excluded = extract_list(
            geoview, geosite, code, tmp / f"exclude-{code}.txt", require_dot=False
        )
        excluded_counts[code] = len(excluded)
        excluded_domains.update(excluded)
        log(f"exclude {code}: {len(excluded)}")

    log("==> extract and expand selected regex rules")
    regex_domains, regex_report = extract_regex_candidates(geosite)
    regex_meta = cast(dict[str, int | list[str]], regex_report["meta"])
    write_text(
        tmp / "regexp-report.json",
        json.dumps(regex_report, ensure_ascii=False, indent=2) + "\n",
    )
    log(
        "regex selected: {selected}, safe expanded domains: {expanded}, rejected: {rejected}".format(
            selected=regex_meta["selected_regexp_rules"],
            expanded=regex_meta["expanded_domains"],
            rejected=regex_meta["rejected_regexp_rules"],
        )
    )

    base_candidates = sorted(set(foreign) | set(not_cn_attr))
    candidates = sorted(set(base_candidates) | set(regex_domains))
    only_from_notcn = sorted(set(not_cn_attr) - set(foreign))
    log(
        f"candidates: {len(candidates)} (only @!cn added: {len(only_from_notcn)}, "
        f"regex added: {len(regex_domains)})"
    )

    exempt_set = set(cn_attr) | excluded_domains
    exempt_list = sorted(exempt_set)

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
        "github.com_in_ban": blocked_by_suffix("github.com", ban_set),
        "category-dev_excluded": not blocked_by_suffix("github.com", ban_set),
        "www.apple.com_in_ban": "www.apple.com" in ban_set,
        "apple.com_in_ban": "apple.com" in ban_set,
        "www.apple.com_in_cn": "www.apple.com" in set(cn_attr),
        "bilibili.tv_in_ban": blocked_by_suffix("bilibili.tv", ban_set),
        "aliexpress.ru_in_ban": blocked_by_suffix("aliexpress.ru", ban_set),
        "geolocation-cn@!cn_deprecated_empty": len(deprecated) == 0,
        "regex_91porn_best_in_ban": blocked_by_suffix("91porn.best", ban_set),
        "regex_91porn_work_in_ban": blocked_by_suffix("91porn.work", ban_set),
    }

    meta = {
        "generated_at": generated_at,
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
            "excluded_domains": len(excluded_domains),
            "excluded_lists": excluded_counts,
            "candidates_foreign_union_notcn": len(base_candidates),
            "candidates_with_regex": len(candidates),
            "added_only_by_notcn": len(only_from_notcn),
            "regex_selected": regex_meta["selected_regexp_rules"],
            "regex_safe_expanded_rules": regex_meta["safe_expanded_regexp_rules"],
            "regex_rejected_rules": regex_meta["rejected_regexp_rules"],
            "regex_expanded_domains": regex_meta["expanded_domains"],
            "raw_diff": len(raw_diff),
            "dnsmasq_safe": len(dnsmasq_safe),
            "domains": len(domains),
            "removed_exact_or_subdomain_of_cn": removed_exact_or_sub,
            "removed_parent_of_exempt": removed_parent_of_exempt,
            "deprecated_geolocation_cn_notcn": len(deprecated),
        },
        "validation": validation,
        "artifacts": {
            DOMAIN_LIST_FILE: "纯域名列表，一行一个域名",
            DNSMASQ_FILE: "dnsmasq 配置，使用 address=/domain/ 返回 NXDOMAIN",
            DNSMASQ_ZERO_FILE: "dnsmasq 配置，将封禁域名解析到 0.0.0.0",
            META_FILE: "构建时间、数量和校验结果",
            REGEXP_REPORT_FILE: "regexp 自动展开明细和拒绝原因",
        },
    }

    # Domain list.
    write_text(dist / DOMAIN_LIST_FILE, "\n".join(domains) + "\n")

    # dnsmasq NXDOMAIN
    conf_lines = [
        "# foreign domains for dnsmasq",
        f"# generated_at: {generated_at}",
        f"# formula: {FORMULA}",
        f"# count: {len(domains)}",
        "# block mode: NXDOMAIN (address=/domain/)",
        "# project: foreign-domains",
        "",
    ]
    conf_lines.extend(f"address=/{d}/" for d in domains)
    write_text(dist / DNSMASQ_FILE, "\n".join(conf_lines) + "\n")

    # dnsmasq 0.0.0.0
    conf0_lines = [
        "# foreign domains for dnsmasq",
        f"# generated_at: {generated_at}",
        f"# formula: {FORMULA}",
        f"# count: {len(domains)}",
        "# block mode: 0.0.0.0",
        "# project: foreign-domains",
        "",
    ]
    conf0_lines.extend(f"address=/{d}/0.0.0.0" for d in domains)
    write_text(dist / DNSMASQ_ZERO_FILE, "\n".join(conf0_lines) + "\n")

    write_text(dist / META_FILE, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    write_text(
        dist / REGEXP_REPORT_FILE,
        json.dumps(regex_report, ensure_ascii=False, indent=2) + "\n",
    )

    # optional richer json for debugging/consumers
    if args.with_json:
        payload = {
            "name": "foreign-domains",
            "formula": FORMULA,
            "count": len(domains),
            "domains": domains,
            "meta": meta,
        }
        write_text(dist / JSON_FILE, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    # fail CI if critical validation fails
    required_ok = (
        validation["youtube.com_in_ban"]
        and validation["www.youtube.com_blocked_by_suffix"]
        and validation["category-dev_excluded"]
        and not validation["www.apple.com_in_ban"]
        and validation["bilibili.tv_in_ban"]
        and validation["geolocation-cn@!cn_deprecated_empty"]
        and validation["regex_91porn_best_in_ban"]
        and validation["regex_91porn_work_in_ban"]
    )
    log("==> validation")
    log(json.dumps(validation, ensure_ascii=True, indent=2))
    log(f"==> done, domains={len(domains)}, dist={dist}")

    if not required_ok:
        log("ERROR: validation failed")
        return 2
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build foreign domain lists")
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
        help=f"also write {JSON_FILE}",
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
