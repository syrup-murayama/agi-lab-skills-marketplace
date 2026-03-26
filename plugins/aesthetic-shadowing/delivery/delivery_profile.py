#!/usr/bin/env python3
"""
delivery_profile.py — Stage4+5 delivery wrapper

指定サンプルディレクトリの JPEG を全て ★4 として審美眼プロファイルを生成し、
jpeg-dir 全体をバッチスコアリングして CSV に書き出す。

使い方:
  python3 delivery_profile.py \
    --samples <samples_dir> \
    --jpeg-dir <jpeg_dir> \
    --context <shooting_context> \
    --output <output.csv> \
    [--profile <profile.json>] \
    [--save-profile <save_path.json>] \
    [--mode text|vision]
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

VENV_PYTHON = Path(__file__).parent.parent / "stage1" / ".venv" / "bin" / "python3"
STAGE4_PROFILE = Path(__file__).parent.parent / "stage4" / "profile.py"
STAGE5_SCORE = Path(__file__).parent.parent / "stage5" / "score.py"

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def get_python() -> str:
    """venv が存在すればそちらを、なければ sys.executable を使う"""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def collect_jpegs(directory: Path) -> list[str]:
    files = sorted(
        p.name for p in directory.iterdir()
        if p.suffix in JPEG_EXTENSIONS
    )
    if not files:
        print(f"エラー: {directory} に JPEG ファイルが見つかりません", file=sys.stderr)
        sys.exit(1)
    return files


def build_rated_samples(jpeg_names: list[str]) -> list[dict]:
    return [{"file": name, "rating": 4} for name in jpeg_names]


def build_session(context: str) -> dict:
    return {"intent": context, "shooting_date": "2026-03-01"}


def run_stage4(
    rated_path: str,
    session_path: str,
    jpeg_dir: str,
    mode: str,
    profile_out: str,
    python: str,
) -> None:
    cmd = [
        python, str(STAGE4_PROFILE),
        "--rated", rated_path,
        "--session", session_path,
        "--jpeg-dir", jpeg_dir,
        "--mode", mode,
        "--output", profile_out,
    ]
    print(f"[delivery] Stage4 実行: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"エラー: Stage4 が終了コード {result.returncode} で失敗しました", file=sys.stderr)
        sys.exit(result.returncode)


def run_stage5(
    jpeg_dir: str,
    profile_path: str,
    scores_out: str,
    python: str,
) -> None:
    cmd = [
        python, str(STAGE5_SCORE),
        jpeg_dir,
        "--profile", profile_path,
        "--output", scores_out,
    ]
    print(f"[delivery] Stage5 実行: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"エラー: Stage5 が終了コード {result.returncode} で失敗しました", file=sys.stderr)
        sys.exit(result.returncode)


def write_output(scores_csv: str, output_path: Path) -> None:
    rows = []
    with open(scores_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "filename": row["filename"],
                "star_rating": row["star_rating"],
                "composite_score": row["composite_score"],
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "star_rating", "composite_score"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[delivery] 完了: {output_path} に {len(rows)} 件書き出しました")


def main() -> None:
    parser = argparse.ArgumentParser(description="delivery_profile.py — Stage4+5 wrapper")
    parser.add_argument("--samples", required=True, help="サンプル JPEG ディレクトリ（全て ★4 として扱う）")
    parser.add_argument("--jpeg-dir", required=True, help="スコアリング対象の JPEG ディレクトリ")
    parser.add_argument("--context", required=True, help="撮影コンテキスト文字列（審美眼プロファイル生成に使用）")
    parser.add_argument("--output", required=True, help="出力 CSV パス（filename, star_rating, composite_score）")
    parser.add_argument("--profile", default=None, help="既存プロファイル JSON（指定時は Stage4 をスキップ）")
    parser.add_argument("--save-profile", default=None, help="生成したプロファイルの保存先 JSON パス")
    parser.add_argument("--mode", choices=["text", "vision"], default="text", help="Stage4 モード（default: text）")
    args = parser.parse_args()

    samples_dir = Path(args.samples)
    jpeg_dir = Path(args.jpeg_dir)

    if not samples_dir.is_dir():
        print(f"エラー: --samples ディレクトリが見つかりません: {samples_dir}", file=sys.stderr)
        sys.exit(1)
    if not jpeg_dir.is_dir():
        print(f"エラー: --jpeg-dir ディレクトリが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    python = get_python()

    tmp_files: list[str] = []
    try:
        # 1. rated_samples_tmp.json を生成
        jpeg_names = collect_jpegs(samples_dir)
        rated_data = build_rated_samples(jpeg_names)
        rated_fd, rated_path = tempfile.mkstemp(suffix=".json", prefix="rated_samples_tmp_")
        tmp_files.append(rated_path)
        with open(rated_fd, "w", encoding="utf-8") as f:
            json.dump(rated_data, f, ensure_ascii=False, indent=2)
        print(f"[delivery] rated_samples_tmp.json: {len(rated_data)} サンプル（全て ★4）")

        # 2. session_tmp.json を生成
        session_data = build_session(args.context)
        session_fd, session_path = tempfile.mkstemp(suffix=".json", prefix="session_tmp_")
        tmp_files.append(session_path)
        with open(session_fd, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        # 3. プロファイルのロードまたは Stage4 実行
        if args.profile:
            profile_path = args.profile
            print(f"[delivery] 既存プロファイルを使用: {profile_path}")
            profile_tmp = None
        else:
            profile_fd, profile_tmp = tempfile.mkstemp(suffix=".json", prefix="profile_tmp_")
            tmp_files.append(profile_tmp)
            import os
            os.close(profile_fd)
            profile_path = profile_tmp
            run_stage4(
                rated_path=rated_path,
                session_path=session_path,
                jpeg_dir=str(jpeg_dir),
                mode=args.mode,
                profile_out=profile_path,
                python=python,
            )

        # 4. Stage5 実行
        scores_fd, scores_tmp = tempfile.mkstemp(suffix=".csv", prefix="scores_tmp_")
        tmp_files.append(scores_tmp)
        import os
        os.close(scores_fd)
        run_stage5(
            jpeg_dir=str(jpeg_dir),
            profile_path=profile_path,
            scores_out=scores_tmp,
            python=python,
        )

        # 5. 出力 CSV 書き出し
        write_output(scores_tmp, Path(args.output))

        # 6. --save-profile があればプロファイルを保存
        if args.save_profile and not args.profile:
            import shutil
            save_path = Path(args.save_profile)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile_path, save_path)
            print(f"[delivery] プロファイルを保存しました: {save_path}")

    finally:
        import os
        for path in tmp_files:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
