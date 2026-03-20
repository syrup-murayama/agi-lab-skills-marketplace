#!/usr/bin/env python3
"""
Stage 6: JPEG メタデータ直接書き込み (ExifTool 使用)

batch_scores.csv の星レーティングを JPEG ファイルの XMP:Rating に直接書き込む。
Lightroom Classic で「メタデータをファイルから読み込む」を実行することで反映される。

使い方:
  python xmp_writer.py \\
    --scores <batch_scores.csv> \\
    --jpeg-dir <input_jpeg_dir>

例:
  python xmp_writer.py \\
    --scores /path/to/batch_scores.csv \\
    --jpeg-dir /path/to/S2_JPEG/
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path


def update_jpeg_rating(jpeg_path: Path, star_rating: int) -> bool:
    """
    ExifTool を使用して JPEG の XMP:Rating を更新する。
    """
    if not jpeg_path.exists():
        print(f"  [WARN] ファイルが見つかりません: {jpeg_path}", file=sys.stderr)
        return False

    try:
        # exiftool -XMP:Rating=N -overwrite_original path
        # -overwrite_original を指定しないと _original バックアップが作成される
        cmd = [
            "exiftool",
            f"-XMP:Rating={star_rating}",
            "-overwrite_original",
            str(jpeg_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [ERROR] ExifTool 実行失敗 ({jpeg_path.name}): {e.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [ERROR] 予期せぬエラー ({jpeg_path.name}): {e}", file=sys.stderr)
        return False


def run(scores_path: Path, jpeg_dir: Path) -> None:
    # CSV 読み込み
    with open(scores_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print(f"[Stage6] ERROR: CSV が空です: {scores_path}", file=sys.stderr)
        sys.exit(1)

    total = len(rows)
    counts = {"updated": 0, "error": 0}

    print(f"[Stage6] {total} 件の JPEG メタデータを更新します (dir: {jpeg_dir})")

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

        jpeg_path = jpeg_dir / filename
        
        if update_jpeg_rating(jpeg_path, star_rating):
            counts["updated"] += 1
        else:
            counts["error"] += 1

        if i % 20 == 0 or i == total:
            print(f"  進捗: {i}/{total} ({100*i//total}%)")

    print(f"[Stage6] 完了: 更新={counts['updated']} エラー={counts['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage6: batch_scores.csv → JPEG メタデータ (XMP:Rating) 直接書き込み"
    )
    parser.add_argument("--scores", required=True, help="batch_scores.csv のパス")
    parser.add_argument("--jpeg-dir", required=True, help="JPEG ファイルが格納されているディレクトリ")
    
    # 互換性のために残すが、現在は使用しない（または警告を出す）
    parser.add_argument("--xmp-dir", help="使用されません (旧互換用)")
    parser.add_argument("--overwrite", action="store_true", help="使用されません (ExifTool は常に上書きします)")
    
    args = parser.parse_args()

    scores_path = Path(args.scores)
    jpeg_dir = Path(args.jpeg_dir)

    if not scores_path.exists():
        print(f"ERROR: CSVが見つかりません: {scores_path}", file=sys.stderr)
        sys.exit(1)

    if not jpeg_dir.is_dir():
        print(f"ERROR: JPEGディレクトリが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    # exiftool の存在確認
    try:
        subprocess.run(["exiftool", "-ver"], check=True, capture_output=True)
    except Exception:
        print("ERROR: exiftool がインストールされていないか、パスが通っていません。", file=sys.stderr)
        sys.exit(1)

    run(scores_path, jpeg_dir)


if __name__ == "__main__":
    main()
