#!/usr/bin/env python3
import argparse
import csv
import html
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


TOKEN_RE = re.compile(r"[a-zа-я0-9#-]{3,}", re.IGNORECASE)
STRING_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.IGNORECASE | re.DOTALL)

STOP_WORDS = {
    "для",
    "или",
    "это",
    "без",
    "new",
    "sale",
    "usd",
    "rub",
    "ml",
    "edp",
    "edt",
    "tester",
    "test",
    "парфюм",
    "парфюмерная",
    "вода",
    "цена",
    "цены",
    "price",
    "list",
    "прайс",
    "лист",
    "наличие",
    "остатки",
    "товар",
    "товары",
    "арт",
    "код",
    "sku",
}


def normalize_token(token: str) -> str:
    token = token.lower().replace("ё", "е")
    return token.strip("-#")


def is_sku_like(token: str) -> bool:
    if len(token) < 4 or len(token) > 30:
        return False
    has_alpha = any("a" <= c <= "z" or "а" <= c <= "я" for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_alpha and has_digit


def extract_tokens_from_xlsx(path: Path, max_chars: int = 4_000_000) -> Tuple[Set[str], Set[str]]:
    text_chunks: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            if "xl/sharedStrings.xml" in zf.namelist():
                raw = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
                if len(raw) > max_chars:
                    raw = raw[:max_chars]
                text_chunks.extend(STRING_RE.findall(raw))
            else:
                worksheet_names = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
                for n in worksheet_names[:2]:
                    raw = zf.read(n).decode("utf-8", errors="ignore")
                    if len(raw) > max_chars // 2:
                        raw = raw[: max_chars // 2]
                    text_chunks.extend(STRING_RE.findall(raw))
    except Exception:
        return set(), set()

    sku_tokens: Set[str] = set()
    word_tokens: Set[str] = set()

    joined = " ".join(text_chunks)
    joined = html.unescape(joined)
    for match in TOKEN_RE.finditer(joined):
        tok = normalize_token(match.group(0))
        if not tok:
            continue
        if tok in STOP_WORDS:
            continue
        if tok.isdigit():
            continue
        if is_sku_like(tok):
            sku_tokens.add(tok)
        elif len(tok) >= 5 and tok.isalpha():
            word_tokens.add(tok)
    return sku_tokens, word_tokens


@dataclass
class Group:
    gid: int
    files: List[str] = field(default_factory=list)
    token_counts: Counter = field(default_factory=Counter)
    seed_tokens: Set[str] = field(default_factory=set)
    core_tokens: Set[str] = field(default_factory=set)
    signature_tokens: Set[str] = field(default_factory=set)

    def refresh_signature(self, max_tokens: int = 220) -> None:
        self.signature_tokens = {t for t, _ in self.token_counts.most_common(max_tokens)}


def pick_features(sku_tokens: Set[str], word_tokens: Set[str]) -> Set[str]:
    # Strong preference to SKU-like anchors, then fill with lexical words.
    features = sorted(sku_tokens)[:240]
    if len(features) < 120:
        missing = 120 - len(features)
        features.extend(sorted(word_tokens)[:missing])
    return set(features)


def rebuild_token_index(groups: Dict[int, Group]) -> Dict[str, Set[int]]:
    index: Dict[str, Set[int]] = defaultdict(set)
    for gid, grp in groups.items():
        anchors = [t for t in grp.seed_tokens if is_sku_like(t)]
        if not anchors:
            anchors = list(grp.seed_tokens)[:40]
        for t in anchors[:80]:
            index[t].add(gid)
    return index


def ensure_hardlink(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def cluster_files(
    src_dir: Path,
    out_dir: Path,
    limit: int,
    min_overlap: int,
    min_ratio: float,
    progress_every: int = 1000,
) -> None:
    files = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx"]
    files.sort(key=lambda p: p.name)
    if limit > 0:
        files = files[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    links_dir = out_dir / "groups"
    links_dir.mkdir(parents=True, exist_ok=True)

    groups: Dict[int, Group] = {}
    token_index: Dict[str, Set[int]] = defaultdict(set)
    next_gid = 1

    map_csv = out_dir / "file_group_map.csv"
    sum_csv = out_dir / "group_summary.csv"
    run_txt = out_dir / "run_stats.txt"

    total = len(files)
    assigned_existing = 0
    new_groups = 0
    empty_feature_files = 0

    with map_csv.open("w", newline="", encoding="utf-8") as f_map:
        writer = csv.writer(f_map)
        writer.writerow(
            [
                "file",
                "group_id",
                "group_name",
                "score",
                "feature_count",
                "sku_count",
                "word_count",
            ]
        )

        for idx, path in enumerate(files, start=1):
            sku_tokens, word_tokens = extract_tokens_from_xlsx(path)
            features = pick_features(sku_tokens, word_tokens)
            if not features:
                empty_feature_files += 1
                gid = next_gid
                next_gid += 1
                grp = Group(gid=gid)
                grp.files.append(path.name)
                groups[gid] = grp
                new_groups += 1
                group_name = f"supplier_{gid:04d}"
                ensure_hardlink(path, links_dir / group_name / path.name)
                writer.writerow([path.name, gid, group_name, 0, 0, 0, 0])
                continue

            anchors = [t for t in features if is_sku_like(t)]
            if not anchors:
                anchors = list(features)[:50]

            candidate_groups: Set[int] = set()
            for tok in anchors[:80]:
                candidate_groups.update(token_index.get(tok, set()))

            best_gid: Optional[int] = None
            best_score = -1
            for gid in candidate_groups:
                grp = groups[gid]
                seed = grp.seed_tokens
                if not seed:
                    continue
                overlap = len(features & seed)
                denom = max(1, min(len(features), len(seed)))
                ratio = overlap / denom
                core = grp.core_tokens if grp.core_tokens else seed
                core_overlap = len(features & core)
                core_ratio = core_overlap / max(1, min(len(features), len(core)))
                score = overlap
                if ratio < min_ratio or core_ratio < (min_ratio * 0.7):
                    continue
                if score > best_score:
                    best_score = score
                    best_gid = gid

            threshold = max(min_overlap, int(len(features) * 0.14))
            if best_gid is not None and best_score >= threshold:
                gid = best_gid
                assigned_existing += 1
            else:
                gid = next_gid
                next_gid += 1
                groups[gid] = Group(gid=gid)
                new_groups += 1

            grp = groups[gid]
            grp.files.append(path.name)
            if not grp.seed_tokens:
                grp.seed_tokens = set(features)
                grp.core_tokens = set(features)
            else:
                grp.core_tokens &= features
                if len(grp.core_tokens) < 20:
                    grp.core_tokens = set(sorted((grp.core_tokens | (features & grp.seed_tokens)))[:80])
            grp.token_counts.update(features)
            if len(grp.files) <= 3 or len(grp.files) % 25 == 0:
                grp.refresh_signature()

            # Lightweight incremental index update.
            update_tokens = [t for t in features if is_sku_like(t)]
            if not update_tokens:
                update_tokens = list(features)[:40]
            for tok in update_tokens[:80]:
                token_index[tok].add(gid)

            group_name = f"supplier_{gid:04d}"
            ensure_hardlink(path, links_dir / group_name / path.name)
            writer.writerow(
                [
                    path.name,
                    gid,
                    group_name,
                    max(best_score, 0),
                    len(features),
                    len(sku_tokens),
                    len(word_tokens),
                ]
            )

            if idx % progress_every == 0:
                print(
                    f"[{idx}/{total}] groups={len(groups)} assigned_existing={assigned_existing} new_groups={new_groups}",
                    flush=True,
                )
                if idx % (progress_every * 5) == 0:
                    # Rebuild index occasionally to reduce stale tokens.
                    for g in groups.values():
                        g.refresh_signature()
                    token_index = rebuild_token_index(groups)

    with sum_csv.open("w", newline="", encoding="utf-8") as f_sum:
        writer = csv.writer(f_sum)
        writer.writerow(["group_id", "group_name", "file_count", "sample_files"])
        for gid, grp in sorted(groups.items(), key=lambda kv: len(kv[1].files), reverse=True):
            writer.writerow(
                [
                    gid,
                    f"supplier_{gid:04d}",
                    len(grp.files),
                    " | ".join(grp.files[:5]),
                ]
            )

    with run_txt.open("w", encoding="utf-8") as f_run:
        f_run.write(f"files_total={total}\n")
        f_run.write(f"groups_total={len(groups)}\n")
        f_run.write(f"assigned_existing={assigned_existing}\n")
        f_run.write(f"new_groups={new_groups}\n")
        f_run.write(f"empty_feature_files={empty_feature_files}\n")
        f_run.write(f"min_overlap={min_overlap}\n")
        f_run.write(f"min_ratio={min_ratio}\n")

    print("Done.", flush=True)
    print(f"files_total={total}", flush=True)
    print(f"groups_total={len(groups)}", flush=True)
    print(f"assigned_existing={assigned_existing}", flush=True)
    print(f"new_groups={new_groups}", flush=True)
    print(f"empty_feature_files={empty_feature_files}", flush=True)
    print(f"Output: {out_dir}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cluster randomized old price files into supplier-like groups.")
    parser.add_argument("--src-dir", required=True, help="Directory with old randomized files.")
    parser.add_argument("--out-dir", required=True, help="Output directory for groups and CSV reports.")
    parser.add_argument("--limit", type=int, default=0, help="Limit files for test run (0 = all).")
    parser.add_argument("--min-overlap", type=int, default=8, help="Minimum token overlap to reuse a group.")
    parser.add_argument("--min-ratio", type=float, default=0.28, help="Minimum feature-overlap ratio to reuse a group.")
    parser.add_argument("--progress-every", type=int, default=1000, help="Progress print interval.")
    args = parser.parse_args()

    src_dir = Path(args.src_dir)
    out_dir = Path(args.out_dir)
    if not src_dir.exists():
        print(f"Source not found: {src_dir}", file=sys.stderr)
        sys.exit(1)

    cluster_files(
        src_dir=src_dir,
        out_dir=out_dir,
        limit=args.limit,
        min_overlap=args.min_overlap,
        min_ratio=max(0.05, min(0.95, args.min_ratio)),
        progress_every=max(100, args.progress_every),
    )


if __name__ == "__main__":
    main()
