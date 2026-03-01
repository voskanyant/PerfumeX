#!/usr/bin/env python3
import argparse
import csv
import re
import zipfile
from collections import Counter
from pathlib import Path
import html

TOKEN_RE = re.compile(r"[a-zа-я0-9#-]{3,}", re.IGNORECASE)
STRING_RE = re.compile(r"<t[^>]*>(.*?)</t>", re.IGNORECASE | re.DOTALL)

STOP = {
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


def norm(t: str) -> str:
    return t.lower().replace("ё", "е").strip("-#")


def parse_tokens(path: Path):
    sku = Counter()
    words = Counter()
    try:
        with zipfile.ZipFile(path) as zf:
            if "xl/sharedStrings.xml" not in zf.namelist():
                return sku, words
            raw = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            for s in STRING_RE.findall(raw):
                s = html.unescape(s)
                for m in TOKEN_RE.finditer(s):
                    t = norm(m.group(0))
                    if not t or t in STOP or t.isdigit():
                        continue
                    has_alpha = any("a" <= c <= "z" or "а" <= c <= "я" for c in t)
                    has_digit = any(c.isdigit() for c in t)
                    if has_alpha and has_digit and 4 <= len(t) <= 30:
                        sku[t] += 1
                    elif t.isalpha() and len(t) >= 5:
                        words[t] += 1
    except Exception:
        return sku, words
    return sku, words


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cluster-dir", required=True)
    p.add_argument("--files-dir", required=True)
    args = p.parse_args()

    cluster_dir = Path(args.cluster_dir)
    files_dir = Path(args.files_dir)
    summary = cluster_dir / "group_summary.csv"
    out = cluster_dir / "group_hints.csv"
    if not summary.exists():
        raise SystemExit(f"missing {summary}")

    with summary.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group_name", "file_count", "sample_file", "top_skus", "top_words"])
        for row in rows:
            group_name = row["group_name"]
            sample_file = (row.get("sample_files") or "").split(" | ")[0].strip()
            if not sample_file:
                w.writerow([group_name, row["file_count"], "", "", ""])
                continue
            fp = files_dir / sample_file
            sku, words = parse_tokens(fp)
            top_skus = ", ".join([k for k, _ in sku.most_common(12)])
            top_words = ", ".join([k for k, _ in words.most_common(12)])
            w.writerow([group_name, row["file_count"], sample_file, top_skus, top_words])

    print(out)


if __name__ == "__main__":
    main()
