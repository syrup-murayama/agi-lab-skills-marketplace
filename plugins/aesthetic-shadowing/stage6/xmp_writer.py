#!/usr/bin/env python3
"""
Stage 6: メタデータ書き出し (Hybrid: JPEG 直接 / RAW XMP)

batch_scores.csv の星レーティングを書き込む。
- JPEG/TIFF: ExifTool を使用してファイル本体の XMP:Rating を更新
- RAW (CR3/ARW/NEF等): XMP サイドカーファイルを生成または更新

Lightroom Classic で「メタデータをファイルから読み込む」を実行することで反映される。

使い方:
  python xmp_writer.py \
    --scores <batch_scores.csv> \
    --image-dir <input_dir> \
    [--groups-csv <stage2_groups.csv>]

  --groups-csv を指定すると Stage2 で読み取った camera_rating > 0 の
  ファイルは AI スコアを上書きせず camera_rating をそのまま使用する。
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path

# 一般的な RAW ファイルの拡張子
RAW_EXTENSIONS = {
    '.CR3', '.CR2', '.ARW', '.NEF', '.RAF', '.ORF', '.GPR', '.DNG',
    '.cr3', '.cr2', '.arw', '.nef', '.raf', '.orf', '.gpr', '.dng'
}

# JPEG/TIFF 等、メタデータ内包が推奨される拡張子
EMBED_EXTENSIONS = {
    '.JPG', '.JPEG', '.TIF', '.TIFF',
    '.jpg', '.jpeg', '.tif', '.tiff'
}

def update_metadata(image_path: Path, star_rating: int) -> str:
    """
    拡張子に応じて直接書き込みか XMP 生成かを切り替える。
    戻り値: "updated_internal" | "created_xmp" | "error"
    """
    if not image_path.exists():
        print(f"  [WARN] ファイルが見つかりません: {image_path}", file=sys.stderr)
        return "error"

    ext = image_path.suffix.lower()

    if ext in EMBED_EXTENSIONS:
        # JPEG等は本体を更新
        return "updated_internal" if _update_internal(image_path, star_rating) else "error"
    else:
        # RAW等は XMP サイドカーを作成/更新
        return "created_xmp" if _update_xmp_sidecar(image_path, star_rating) else "error"

def _build_exiftool_args(star_rating: int) -> list[str]:
    """
    star_rating に応じた ExifTool タグ引数を返す。

    -1 → xmp:Pick=-1 (Lightroom Classic 13.2+ の Reject フラグ)
         namespace: http://ns.adobe.com/xap/1.0/
         4〜5 はユーザーが Lightroom で現像時につける聖域 → AI は最大 ★3 を書き込む
    0+ → XMP:Rating=N (通常の星レーティング、0=なし、1〜3)
    """
    if star_rating == -1:
        return ["-XMP-xmp:Pick=-1"]
    return [f"-XMP:Rating={star_rating}"]


def _update_internal(image_path: Path, star_rating: int) -> bool:
    """ExifTool を使用してファイル本体を更新"""
    try:
        cmd = ["exiftool"] + _build_exiftool_args(star_rating) + ["-overwrite_original", str(image_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except Exception as e:
        print(f"  [ERROR] Internal update failed ({image_path.name}): {e}", file=sys.stderr)
        return False

def _update_xmp_sidecar(image_path: Path, star_rating: int) -> bool:
    """ExifTool を使用して XMP サイドカーファイルを生成/更新"""
    xmp_path = image_path.with_suffix(".xmp")
    try:
        cmd = ["exiftool"] + _build_exiftool_args(star_rating) + ["-overwrite_original", str(xmp_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except Exception as e:
        print(f"  [ERROR] XMP update failed ({xmp_path.name}): {e}", file=sys.stderr)
        return False

def load_camera_ratings(groups_csv: Path) -> dict:
    """
    Stage2 の groups CSV から camera_rating を読み込み、
    stem (拡張子なしファイル名) をキーとする dict を返す。
    """
    ratings = {}
    with open(groups_csv, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row.get("filename", "")
            try:
                cr = int(row.get("camera_rating", 0) or 0)
            except ValueError:
                cr = 0
            if filename:
                stem = Path(filename).stem
                ratings[stem] = cr
    return ratings


def run(scores_path: Path, image_dir: Path, groups_csv: Path | None = None) -> None:
    camera_ratings: dict = {}
    if groups_csv is not None:
        if not groups_csv.exists():
            print(f"[Stage6] WARN: --groups-csv が見つかりません: {groups_csv}", file=sys.stderr)
        else:
            camera_ratings = load_camera_ratings(groups_csv)
            print(f"[Stage6] camera_rating を読み込みました: {len(camera_ratings)} 件 (from {groups_csv.name})")

    with open(scores_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print(f"[Stage6] ERROR: CSV が空です: {scores_path}", file=sys.stderr)
        sys.exit(1)

    total = len(rows)
    counts = {"internal": 0, "xmp": 0, "error": 0, "camera_override": 0}

    print(f"[Stage6] {total} 件のメタデータを更新します (dir: {image_dir})")

    for i, row in enumerate(rows, 1):
        filename = row.get("filename", "")
        try:
            star_rating = int(row.get("star_rating", 0))
        except ValueError:
            counts["error"] += 1
            continue

        if not filename:
            counts["error"] += 1
            continue

        # AI スコアは最大 ★3 にキャップ
        # 4〜5 はユーザーが Lightroom で現像時につける聖域
        if star_rating > 3:
            star_rating = 3

        # camera_rating > 0 であれば AI スコアを上書きしない
        stem = Path(filename).stem
        cr = camera_ratings.get(stem, 0)
        if cr > 0:
            star_rating = cr
            counts["camera_override"] += 1

        image_path = image_dir / filename
        status = update_metadata(image_path, star_rating)
        
        if status == "updated_internal":
            counts["internal"] += 1
        elif status == "created_xmp":
            counts["xmp"] += 1
        else:
            counts["error"] += 1

        if i % 20 == 0 or i == total:
            print(f"  進捗: {i}/{total} ({100*i//total}%)")

    print(f"[Stage6] 完了: 本体更新={counts['internal']} XMP生成={counts['xmp']} "
          f"カメラ優先={counts['camera_override']} エラー={counts['error']}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage6: batch_scores.csv → メタデータ書き出し (Hybrid)"
    )
    parser.add_argument("--scores", required=True, help="batch_scores.csv のパス")
    parser.add_argument("--image-dir", required=True, help="画像ファイルが格納されているディレクトリ")
    parser.add_argument("--groups-csv", help="Stage2 の groups CSV (camera_rating 優先適用)")

    # 旧互換用（無視される）
    parser.add_argument("--jpeg-dir", help="image-dir と同じ（旧互換）")
    parser.add_argument("--xmp-dir", help="使用されません")
    parser.add_argument("--overwrite", action="store_true", help="常に上書きします")

    args = parser.parse_args()

    scores_path = Path(args.scores)
    # jpeg-dir が指定されていればそれを使う（旧互換）
    image_dir = Path(args.image_dir or args.jpeg_dir)
    groups_csv = Path(args.groups_csv) if args.groups_csv else None

    if not scores_path.exists():
        print(f"ERROR: CSVが見つかりません: {scores_path}", file=sys.stderr)
        sys.exit(1)

    if not image_dir.is_dir():
        print(f"ERROR: ディレクトリが見つかりません: {image_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        subprocess.run(["exiftool", "-ver"], check=True, capture_output=True)
    except Exception:
        print("ERROR: exiftool がインストールされていません。", file=sys.stderr)
        sys.exit(1)

    run(scores_path, image_dir, groups_csv)

if __name__ == "__main__":
    main()
