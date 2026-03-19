#!/usr/bin/env python3
"""
Stage 6: XMP サイドカー書き出し

batch_scores.csv の星レーティングを XMP サイドカーファイルに書き込む。
Lightroom がそのまま読み込めるフォーマット。

使い方:
  python xmp_writer.py \\
    --scores <batch_scores.csv> \\
    --xmp-dir <output_xmp_dir> \\
    [--overwrite]

例:
  python xmp_writer.py \\
    --scores /path/to/batch_scores.csv \\
    --xmp-dir /path/to/xmp_output/ \\
    --overwrite
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


# ---- XMP 名前空間 ----
NS = {
    "x":      "adobe:ns:meta/",
    "rdf":    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp":    "http://ns.adobe.com/xap/1.0/",
    "xmpDM":  "http://ns.adobe.com/xmp/1.0/DynamicMedia/",
}

XMP_TEMPLATE = """\
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">
      <xmp:Rating>{rating}</xmp:Rating>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""


def update_or_create_xmp(xmp_path: Path, star_rating: int, overwrite: bool) -> str:
    """
    XMP ファイルを更新または新規作成する。

    - 既存ファイルがある場合: xmp:Rating だけ更新し、他フィールド（xmpDM:pick など）を保持
    - ファイルがない場合: 新規作成
    - overwrite=False かつ既存ファイルあり: スキップ

    戻り値: "created" | "updated" | "skipped"
    """
    if xmp_path.exists() and not overwrite:
        return "skipped"

    if xmp_path.exists():
        return _update_existing_xmp(xmp_path, star_rating)
    else:
        return _create_new_xmp(xmp_path, star_rating)


def _create_new_xmp(xmp_path: Path, star_rating: int) -> str:
    xmp_path.parent.mkdir(parents=True, exist_ok=True)
    xmp_path.write_text(XMP_TEMPLATE.format(rating=star_rating), encoding="utf-8")
    return "created"


def _update_existing_xmp(xmp_path: Path, star_rating: int) -> str:
    """既存 XMP の xmp:Rating だけを更新する（他フィールド保持）"""
    content = xmp_path.read_text(encoding="utf-8")

    # xmp:Rating タグが既に存在する場合は正規表現で置換（XML パーサーが名前空間で崩れる場合を考慮）
    rating_pattern = re.compile(r"<xmp:Rating>\d+</xmp:Rating>")
    if rating_pattern.search(content):
        new_content = rating_pattern.sub(f"<xmp:Rating>{star_rating}</xmp:Rating>", content)
        xmp_path.write_text(new_content, encoding="utf-8")
        return "updated"

    # xmp:Rating がない場合: </rdf:Description> の直前に挿入
    insert_tag = f"      <xmp:Rating>{star_rating}</xmp:Rating>\n"
    close_desc = "</rdf:Description>"
    if close_desc in content:
        new_content = content.replace(close_desc, insert_tag + close_desc, 1)
        xmp_path.write_text(new_content, encoding="utf-8")
        return "updated"

    # 構造が想定外の場合は上書き
    _create_new_xmp(xmp_path, star_rating)
    return "updated"


def run(scores_path: Path, xmp_dir: Path, overwrite: bool) -> None:
    xmp_dir.mkdir(parents=True, exist_ok=True)

    # CSV 読み込み
    with open(scores_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print(f"[Stage6] ERROR: CSV が空です: {scores_path}", file=sys.stderr)
        sys.exit(1)

    total = len(rows)
    counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}

    print(f"[Stage6] {total} 件の XMP を書き出します → {xmp_dir}")
    if not overwrite:
        print("[Stage6] 既存 XMP はスキップします（上書きするには --overwrite）")

    for i, row in enumerate(rows, 1):
        filename = row.get("filename", "")
        try:
            star_rating = int(row.get("star_rating", 0))
        except ValueError:
            print(f"  [WARN] star_rating が無効: {row}", file=sys.stderr)
            counts["error"] += 1
            continue

        if not filename:
            counts["error"] += 1
            continue

        stem = Path(filename).stem
        xmp_path = xmp_dir / f"{stem}.xmp"

        try:
            status = update_or_create_xmp(xmp_path, star_rating, overwrite)
            counts[status] += 1
        except Exception as e:
            print(f"  [WARN] {filename} → {e}", file=sys.stderr)
            counts["error"] += 1

        if i % 50 == 0 or i == total:
            print(f"  進捗: {i}/{total} ({100*i//total}%)")

    print(f"[Stage6] 完了: 新規={counts['created']} 更新={counts['updated']} "
          f"スキップ={counts['skipped']} エラー={counts['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage6: batch_scores.csv → XMP サイドカー書き出し"
    )
    parser.add_argument("--scores", required=True, help="batch_scores.csv のパス")
    parser.add_argument("--xmp-dir", required=True, help="XMP 出力ディレクトリ")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存 XMP を上書き（デフォルトはスキップ）",
    )
    args = parser.parse_args()

    scores_path = Path(args.scores)
    xmp_dir = Path(args.xmp_dir)

    if not scores_path.exists():
        print(f"ERROR: CSVが見つかりません: {scores_path}", file=sys.stderr)
        sys.exit(1)

    run(scores_path, xmp_dir, args.overwrite)


if __name__ == "__main__":
    main()
