#!/usr/bin/env python3
"""
delivery_profile.py — Stage5 delivery wrapper

指定サンプルディレクトリの JPEG を参考に撮影コンテキストから審美眼プロファイルを生成し、
jpeg-dir 全体をバッチスコアリングして CSV に書き出す。

使い方:
  python3 delivery_profile.py \
    --samples <samples_dir> \
    --jpeg-dir <jpeg_dir> \
    --context <shooting_context> \
    --output <output.csv> \
    [--profile <profile.json>] \
    [--save-profile <save_path.json>]
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VENV_PYTHON = Path(__file__).parent.parent / "stage1" / ".venv" / "bin" / "python3"
STAGE5_SCORE = Path(__file__).parent.parent / "stage5" / "score.py"

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def get_python() -> str:
    """venv が存在すればそちらを、なければ sys.executable を使う"""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def count_jpegs(directory: Path) -> int:
    count = sum(1 for p in directory.iterdir() if p.suffix in JPEG_EXTENSIONS)
    if count == 0:
        print(f"エラー: {directory} に JPEG ファイルが見つかりません", file=sys.stderr)
        sys.exit(1)
    return count


def build_profile(context: str) -> dict:
    """--context テキストから直接 aesthetic_profile.json を組み立てる（Claude API 不要）"""
    return {
        "clip_query": context,
        "high_keywords": [],
        "low_keywords": [],
    }


def run_stage5(jpeg_dir: str, profile_path: str, scores_out: str, python: str) -> None:
    cmd = [python, str(STAGE5_SCORE), jpeg_dir, "--profile", profile_path, "--output", scores_out]
    print(f"[delivery] Stage5 実行: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"エラー: Stage5 が終了コード {result.returncode} で失敗しました", file=sys.stderr)
        sys.exit(result.returncode)


def write_output(scores_csv: str, output_path: Path) -> None:
    if not Path(scores_csv).exists() or Path(scores_csv).stat().st_size == 0:
        print(f"エラー: Stage5 の出力が空です: {scores_csv}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(scores_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                star = int(row.get("star_rating") or 0)
                score = float(row.get("composite_score") or 0.0)
            except ValueError:
                star, score = 0, 0.0
            rows.append({"filename": row["filename"], "star_rating": star, "composite_score": score})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "star_rating", "composite_score"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[delivery] 完了: {output_path} に {len(rows)} 件書き出しました")


def main() -> None:
    parser = argparse.ArgumentParser(description="delivery_profile.py — Stage5 wrapper")
    parser.add_argument("--samples", required=True, help="サンプル JPEG ディレクトリ（枚数確認のみ）")
    parser.add_argument("--jpeg-dir", required=True, help="スコアリング対象の JPEG ディレクトリ")
    parser.add_argument("--context", required=True, help="撮影コンテキスト文字列（clip_query に使用）")
    parser.add_argument("--output", required=True, help="出力 CSV パス（filename, star_rating, composite_score）")
    parser.add_argument("--profile", default=None, help="既存プロファイル JSON（指定時はプロファイル生成をスキップ）")
    parser.add_argument("--save-profile", default=None, help="生成したプロファイルの保存先 JSON パス")
    args = parser.parse_args()

    samples_dir = Path(args.samples)
    jpeg_dir = Path(args.jpeg_dir)

    if not samples_dir.is_dir():
        print(f"エラー: --samples ディレクトリが見つかりません: {samples_dir}", file=sys.stderr)
        sys.exit(1)
    if not jpeg_dir.is_dir():
        print(f"エラー: --jpeg-dir ディレクトリが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"[delivery] サンプル: {count_jpegs(samples_dir)} 枚")
    python = get_python()

    tmp_files: list[str] = []
    try:
        # 1. プロファイルのロードまたは --context から生成
        if args.profile:
            profile_path = args.profile
            print(f"[delivery] 既存プロファイルを使用: {profile_path}")
            profile_tmp = None
        else:
            profile_fd, profile_tmp = tempfile.mkstemp(suffix=".json", prefix="profile_tmp_")
            tmp_files.append(profile_tmp)
            os.close(profile_fd)
            profile_path = profile_tmp
            profile_data = build_profile(args.context)
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, ensure_ascii=False, indent=2)
            print(f"[delivery] プロファイル生成: clip_query={args.context[:50]}...")

        # 2. Stage5 でスコアリング
        scores_fd, scores_tmp = tempfile.mkstemp(suffix=".csv", prefix="scores_tmp_")
        tmp_files.append(scores_tmp)
        os.close(scores_fd)
        run_stage5(jpeg_dir=str(jpeg_dir), profile_path=profile_path,
                   scores_out=scores_tmp, python=python)

        # 3. 出力 CSV 書き出し
        write_output(scores_tmp, Path(args.output))

        # 4. --save-profile があればプロファイルを保存
        if args.save_profile and not args.profile:
            save_path = Path(args.save_profile)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile_path, save_path)
            print(f"[delivery] プロファイルを保存しました: {save_path}")

    finally:
        for path in tmp_files:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
