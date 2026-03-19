#!/usr/bin/env python3
"""
Stage 5: CLIP バッチスコアリング

aesthetic_profile.json の clip_query を使い、JPEG ディレクトリ全枚を
ローカル CLIP でスコアリングして星レーティング（0〜4）を判定する。

使い方:
  python score.py \\
    --profile <aesthetic_profile.json> \\
    --jpeg-dir <jpeg_dir> \\
    --output <batch_scores.csv> \\
    [--thresholds "0.25,0.45,0.60,0.75"] \\
    [--verbose]

例:
  python score.py \\
    --profile /path/to/aesthetic_profile.json \\
    --jpeg-dir /path/to/S2_JPEG/ \\
    --output /path/to/batch_scores.csv
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


# ---- デフォルト閾値 ----
# 0.0〜0.25 → ★0、0.25〜0.45 → ★1、0.45〜0.60 → ★2、0.60〜0.75 → ★3、0.75〜1.0 → ★4
DEFAULT_THRESHOLDS = [0.25, 0.45, 0.60, 0.75]

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def ensure_open_clip(venv_pip: str) -> None:
    """open-clip-torch がなければ自動インストール"""
    try:
        import open_clip  # noqa: F401
    except ImportError:
        print("[Stage5] open-clip-torch が見つかりません。インストールします...")
        subprocess.check_call([venv_pip, "install", "open-clip-torch"])
        print("[Stage5] インストール完了")


def get_device():
    """利用可能なデバイスを返す（cuda > mps > cpu）"""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    # Apple Silicon MPS
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def score_to_star(score: float, thresholds: list[float]) -> int:
    """CLIPスコア（0〜1）を星レーティング（0〜4）に変換"""
    for i, t in enumerate(sorted(thresholds)):
        if score < t:
            return i
    return len(thresholds)  # 最高レーティング


def run(
    profile_path: Path,
    jpeg_dir: Path,
    output_path: Path,
    thresholds: list[float],
    verbose: bool,
) -> None:
    import torch
    import open_clip
    from PIL import Image

    # ---- プロファイル読み込み ----
    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    clip_query: str | list[str] = profile.get("clip_query", "")
    if not clip_query:
        print("[Stage5] ERROR: aesthetic_profile.json に clip_query がありません", file=sys.stderr)
        sys.exit(1)

    # clip_query はリストでも文字列でも受け付ける
    if isinstance(clip_query, list):
        queries = clip_query
    else:
        queries = [clip_query]

    if verbose:
        print(f"[Stage5] clip_query: {queries}")

    # ---- CLIP モデルロード ----
    device = get_device()
    print(f"[Stage5] デバイス: {device}")
    print("[Stage5] CLIPモデル (ViT-B-32 / openai) をロード中...")

    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai"
    )
    model = model.to(device)
    model.eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")

    # テキスト特徴量を事前計算
    with torch.no_grad():
        text_tokens = tokenizer(queries).to(device)
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        # 複数クエリの場合は平均
        text_feature = text_features.mean(dim=0, keepdim=True)
        text_feature = text_feature / text_feature.norm(dim=-1, keepdim=True)

    # ---- JPEG ファイル列挙 ----
    jpeg_files = sorted(
        [p for p in jpeg_dir.iterdir() if p.suffix in JPEG_EXTENSIONS]
    )
    total = len(jpeg_files)
    if total == 0:
        print(f"[Stage5] ERROR: JPEG が見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"[Stage5] {total} 枚をスコアリング開始")

    # ---- スコアリング ----
    results = []
    for i, jpeg_path in enumerate(jpeg_files, 1):
        try:
            image = Image.open(jpeg_path).convert("RGB")
            image_tensor = preprocess(image).unsqueeze(0).to(device)

            with torch.no_grad():
                image_feature = model.encode_image(image_tensor)
                image_feature = image_feature / image_feature.norm(dim=-1, keepdim=True)
                similarity = (image_feature @ text_feature.T).item()

            # cosine similarity は -1〜1 → 0〜1 に正規化
            score = (similarity + 1.0) / 2.0
            star = score_to_star(score, thresholds)
            results.append((jpeg_path.name, score, star))

            if verbose:
                print(f"  [{i:4d}/{total}] {jpeg_path.name}  score={score:.4f}  ★{star}")
            elif i % 50 == 0 or i == total:
                print(f"  進捗: {i}/{total} ({100*i//total}%)")

        except Exception as e:
            print(f"  [WARN] {jpeg_path.name} をスキップ: {e}", file=sys.stderr)
            results.append((jpeg_path.name, 0.0, 0))

    # ---- CSV 出力 ----
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "clip_score", "star_rating"])
        for name, score, star in results:
            writer.writerow([name, f"{score:.6f}", star])

    print(f"[Stage5] 完了: {output_path} に {len(results)} 件書き出しました")

    # ---- サマリ ----
    from collections import Counter
    dist = Counter(star for _, _, star in results)
    print("[Stage5] 星レーティング分布:")
    for s in range(5):
        count = dist.get(s, 0)
        bar = "█" * (count * 40 // max(total, 1))
        print(f"  ★{s}: {count:4d}件  {bar}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage5: CLIP バッチスコアリング → 星レーティング判定"
    )
    parser.add_argument("--profile", required=True, help="aesthetic_profile.json のパス")
    parser.add_argument("--jpeg-dir", required=True, help="JPEG ディレクトリのパス")
    parser.add_argument("--output", required=True, help="出力 CSV パス (batch_scores.csv)")
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help='星変換の閾値をカンマ区切りで指定 (デフォルト: "0.25,0.45,0.60,0.75")',
    )
    parser.add_argument("--verbose", action="store_true", help="全ファイルのスコアを表示")
    args = parser.parse_args()

    profile_path = Path(args.profile)
    jpeg_dir = Path(args.jpeg_dir)
    output_path = Path(args.output)

    if not profile_path.exists():
        print(f"ERROR: プロファイルが見つかりません: {profile_path}", file=sys.stderr)
        sys.exit(1)
    if not jpeg_dir.is_dir():
        print(f"ERROR: ディレクトリが存在しません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    thresholds = [float(t) for t in args.thresholds.split(",")]

    # open-clip-torch を自動インストール（stage1/.venv の pip を使用）
    script_dir = Path(__file__).parent
    venv_pip = script_dir.parent / "stage1" / ".venv" / "bin" / "pip"
    if venv_pip.exists():
        ensure_open_clip(str(venv_pip))
    else:
        ensure_open_clip("pip")

    run(profile_path, jpeg_dir, output_path, thresholds, args.verbose)


if __name__ == "__main__":
    main()
