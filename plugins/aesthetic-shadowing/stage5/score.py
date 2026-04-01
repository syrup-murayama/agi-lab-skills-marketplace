#!/usr/bin/env python3
"""
Stage 5: CLIP バッチスコアリング（複合スコア対応版 + 画像-画像モード）

aesthetic_profile.json の clip_query / high_keywords / low_keywords を使い、
複合スコアで星レーティング（0〜4）を判定する。

複合スコア計算式:
  composite_raw = w1 * clip_score + w2 * high_score - w3 * low_score  (クランプ [0,1])
  composite_score = min-max 正規化（--no-normalize で無効）
  star_rating = thresholds に基づく変換

min-max 正規化について:
  日本語テキストでは CLIP の識別力が低く、composite_raw が狭い範囲に集中する。
  正規化することで相対ランキングを保ちながら全星レンジを活用する。
  --no-normalize で生のクランプ値を使用（旧互換）。

画像-画像モード (--mode image):
  rated_samples.json の評価済み画像を視覚アンカーとして使用。
  composite_score = mean_sim(pos) - mean_sim(neg)  → min-max 正規化

使い方:
  python score.py <jpeg_dir> \\
    --profile <aesthetic_profile.json> \\
    --output <batch_scores.csv> \\
    [--mode text|image] \\
    [--rated-samples <rated_samples.json>] \\
    [--thresholds "0.25,0.45,0.60,0.75"] \\
    [--weights "0.5,0.3,0.2"] \\
    [--no-composite] \\
    [--no-normalize] \\
    [--verbose]
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


# ---- デフォルト設定 ----
DEFAULT_THRESHOLDS = [0.25, 0.45, 0.60, 0.75]
DEFAULT_WEIGHTS = [0.5, 0.3, 0.2]  # clip, high, low
DEFAULT_MODEL = "ViT-B-32"
DEFAULT_PRETRAINED = "openai"

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
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def score_to_star(score: float, thresholds: list[float]) -> int:
    """スコア（0〜1）を星レーティング（0〜4）に変換"""
    for i, t in enumerate(sorted(thresholds)):
        if score < t:
            return i
    return len(thresholds)


def encode_texts(model, tokenizer, texts: list[str], device: str):
    """テキストリストを正規化済み特徴量テンソルに変換（平均）"""
    import torch
    with torch.no_grad():
        tokens = tokenizer(texts).to(device)
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
        mean_feat = features.mean(dim=0, keepdim=True)
        mean_feat = mean_feat / mean_feat.norm(dim=-1, keepdim=True)
    return mean_feat


def cosine_to_score(cosine: float) -> float:
    """コサイン類似度 (-1〜1) を 0〜1 に正規化"""
    return (cosine + 1.0) / 2.0


def encode_images(model, preprocess, paths: list[Path], device: str):
    """画像リストを正規化済み特徴量テンソルに変換（バッチ平均）"""
    import torch
    from PIL import Image, ImageOps

    feats = []
    for p in paths:
        try:
            img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            tensor = preprocess(img).unsqueeze(0).to(device)
            with torch.no_grad():
                f = model.encode_image(tensor)
                f = f / f.norm(dim=-1, keepdim=True)
            feats.append(f)
        except Exception as e:
            print(f"  [WARN] アンカー画像スキップ: {p.name}: {e}", file=sys.stderr)
    if not feats:
        return None
    return torch.cat(feats, dim=0)  # (N, D)


def load_anchor_embeds(
    rated_samples_path: Path,
    jpeg_dir: Path,
    model,
    preprocess,
    device: str,
):
    """rated_samples.json から positive/negative アンカー埋め込みを返す"""
    with open(rated_samples_path, encoding="utf-8") as f:
        data = json.load(f)

    samples = [s for s in data.get("samples", []) if not s.get("skipped")]
    pos_paths, neg_paths = [], []
    for s in samples:
        fname = s.get("file", s.get("path", ""))
        rating = s.get("human_rating", 0)
        # パス解決: 絶対パスならそのまま、それ以外は jpeg_dir から解決
        p = Path(fname)
        if not p.is_absolute():
            p = jpeg_dir / p.name
        if rating >= 4:
            pos_paths.append(p)
        elif rating <= 2:
            neg_paths.append(p)

    print(f"[Stage5] アンカー: positive={len(pos_paths)}枚, negative={len(neg_paths)}枚")
    pos_embeds = encode_images(model, preprocess, pos_paths, device)
    neg_embeds = encode_images(model, preprocess, neg_paths, device)
    return pos_embeds, neg_embeds


def run_image_mode(
    jpeg_dir: Path,
    rated_samples_path: Path,
    output_path: Path,
    thresholds: list[float],
    normalize: bool,
    verbose: bool,
    model_name: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
) -> None:
    """画像-画像 CLIP スコアリング"""
    import torch
    import open_clip
    from PIL import Image, ImageOps

    device = get_device()
    print(f"[Stage5] デバイス: {device}")
    print(f"[Stage5] モデルロード中: {model_name} / {pretrained}")

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained
    )
    model = model.to(device)
    model.eval()

    # アンカー埋め込みを事前計算
    pos_embeds, neg_embeds = load_anchor_embeds(
        rated_samples_path, jpeg_dir, model, preprocess, device
    )
    if pos_embeds is None:
        print("[Stage5] ERROR: positive アンカーが 0 枚です", file=sys.stderr)
        sys.exit(1)

    # ---- JPEG ファイル列挙 ----
    jpeg_files = sorted(
        [p for p in jpeg_dir.iterdir() if p.suffix in JPEG_EXTENSIONS]
    )
    total = len(jpeg_files)
    if total == 0:
        print(f"[Stage5] ERROR: JPEG が見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"[Stage5] {total} 枚をスコアリング開始（画像-画像モード）")

    results = []
    for i, jpeg_path in enumerate(jpeg_files, 1):
        try:
            img = ImageOps.exif_transpose(Image.open(jpeg_path)).convert("RGB")
            tensor = preprocess(img).unsqueeze(0).to(device)
            with torch.no_grad():
                img_feat = model.encode_image(tensor)
                img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)

                pos_sim = (img_feat @ pos_embeds.T).mean().item()  # mean cosine
                if neg_embeds is not None:
                    neg_sim = (img_feat @ neg_embeds.T).mean().item()
                    raw_score = pos_sim - neg_sim
                else:
                    raw_score = pos_sim

            results.append((jpeg_path.name, raw_score, 0.0, 0.0, raw_score, 0))

            if verbose:
                print(
                    f"  [{i:4d}/{total}] {jpeg_path.name}"
                    f"  pos_sim={pos_sim:.4f}"
                    + (f"  neg_sim={neg_sim:.4f}" if neg_embeds is not None else "")
                    + f"  raw={raw_score:.4f}"
                )
            elif i % 50 == 0 or i == total:
                print(f"  進捗: {i}/{total} ({100*i//total}%)")

        except Exception as e:
            print(f"  [WARN] {jpeg_path.name} をスキップ: {e}", file=sys.stderr)
            results.append((jpeg_path.name, 0.0, 0.0, 0.0, 0.0, 0))

    # ---- min-max 正規化 ----
    if normalize and results:
        raw_scores = [r[4] for r in results]
        c_min, c_max = min(raw_scores), max(raw_scores)
        c_range = c_max - c_min
        if c_range > 1e-8:
            print(f"[Stage5] 正規化: min={c_min:.4f}, max={c_max:.4f}, range={c_range:.4f}")
            results = [
                (name, cs, hs, ls, (comp - c_min) / c_range, 0)
                for name, cs, hs, ls, comp, _ in results
            ]
        else:
            print("[Stage5] WARN: スコアの range が極小のため正規化をスキップ", file=sys.stderr)

    # star_rating を最終スコアから計算
    results = [
        (name, cs, hs, ls, comp, score_to_star(comp, thresholds))
        for name, cs, hs, ls, comp, _ in results
    ]

    _write_csv_and_summary(output_path, results, total)


def run(
    profile_path: Path,
    jpeg_dir: Path,
    output_path: Path,
    thresholds: list[float],
    weights: list[float],
    use_composite: bool,
    normalize: bool,
    verbose: bool,
    model_name: str = DEFAULT_MODEL,
    pretrained: str = DEFAULT_PRETRAINED,
) -> None:
    import torch
    import open_clip
    from PIL import Image, ImageOps

    w_clip, w_high, w_low = weights

    # ---- プロファイル読み込み ----
    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    clip_query = profile.get("clip_query", "")
    if not clip_query:
        print("[Stage5] ERROR: aesthetic_profile.json に clip_query がありません", file=sys.stderr)
        sys.exit(1)

    queries = clip_query if isinstance(clip_query, list) else [clip_query]
    high_keywords: list[str] = profile.get("high_keywords", [])
    low_keywords: list[str] = profile.get("low_keywords", [])

    # composite モードの feasibility チェック
    if use_composite and (not high_keywords or not low_keywords):
        print(
            "[Stage5] WARN: high_keywords/low_keywords がないため clip_query 1本でフォールバックします",
            file=sys.stderr,
        )
        use_composite = False

    if verbose:
        print(f"[Stage5] clip_query: {queries}")
        if use_composite:
            print(f"[Stage5] high_keywords ({len(high_keywords)}): {high_keywords}")
            print(f"[Stage5] low_keywords  ({len(low_keywords)}): {low_keywords}")
            print(f"[Stage5] weights: clip={w_clip}, high={w_high}, low={w_low}")
            print(f"[Stage5] 正規化: {'有効' if normalize else '無効 (--no-normalize)'}")
        else:
            print("[Stage5] モード: clip_query のみ（--no-composite または fallback）")

    # ---- CLIP モデルロード ----
    device = get_device()
    print(f"[Stage5] デバイス: {device}")
    print(f"[Stage5] モデルロード中: {model_name} / {pretrained}")

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained
    )
    model = model.to(device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(model_name)

    # テキスト特徴量を事前計算
    clip_feat = encode_texts(model, tokenizer, queries, device)
    if use_composite:
        high_feat = encode_texts(model, tokenizer, high_keywords, device)
        low_feat = encode_texts(model, tokenizer, low_keywords, device)

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
            image = ImageOps.exif_transpose(Image.open(jpeg_path)).convert("RGB")
            image_tensor = preprocess(image).unsqueeze(0).to(device)

            with torch.no_grad():
                img_feat = model.encode_image(image_tensor)
                img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)

                clip_score = cosine_to_score((img_feat @ clip_feat.T).item())

                if use_composite:
                    high_score = cosine_to_score((img_feat @ high_feat.T).item())
                    low_score = cosine_to_score((img_feat @ low_feat.T).item())
                    composite = w_clip * clip_score + w_high * high_score - w_low * low_score
                    composite = max(0.0, min(1.0, composite))
                else:
                    high_score = None
                    low_score = None
                    composite = clip_score

            star = score_to_star(composite, thresholds)
            results.append((jpeg_path.name, clip_score, high_score, low_score, composite, star))

            if verbose:
                if use_composite:
                    print(
                        f"  [{i:4d}/{total}] {jpeg_path.name}"
                        f"  clip={clip_score:.4f}"
                        f"  high={high_score:.4f}"
                        f"  low={low_score:.4f}"
                        f"  composite={composite:.4f}"
                        f"  ★{star}"
                    )
                else:
                    print(f"  [{i:4d}/{total}] {jpeg_path.name}  score={composite:.4f}  ★{star}")
            elif i % 50 == 0 or i == total:
                print(f"  進捗: {i}/{total} ({100*i//total}%)")

        except Exception as e:
            print(f"  [WARN] {jpeg_path.name} をスキップ: {e}", file=sys.stderr)
            results.append((jpeg_path.name, 0.0, None, None, 0.0, 0))

    # ---- composite モードの min-max 正規化 ----
    if use_composite and normalize and results:
        raw_composites = [r[4] for r in results]
        c_min, c_max = min(raw_composites), max(raw_composites)
        c_range = c_max - c_min
        if c_range > 1e-8:
            print(f"[Stage5] composite 正規化: min={c_min:.4f}, max={c_max:.4f}, range={c_range:.4f}")
            results = [
                (name, cs, hs, ls, (comp - c_min) / c_range, 0)  # star は後で再計算
                for name, cs, hs, ls, comp, _ in results
            ]
        else:
            print("[Stage5] WARN: composite の range が極小のため正規化をスキップ", file=sys.stderr)

    # star_rating を最終 composite_score から計算
    results = [
        (name, cs, hs, ls, comp, score_to_star(comp, thresholds))
        for name, cs, hs, ls, comp, _ in results
    ]

    _write_csv_and_summary(output_path, results, total)


def _write_csv_and_summary(output_path: Path, results: list, total: int) -> None:
    """CSV 書き出しとサマリ表示"""
    # ---- CSV 出力 ----
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "clip_score", "high_score", "low_score", "composite_score", "star_rating"])
        for name, clip_s, high_s, low_s, comp, star in results:
            writer.writerow([
                name,
                f"{clip_s:.6f}",
                f"{high_s:.6f}" if high_s is not None else "",
                f"{low_s:.6f}" if low_s is not None else "",
                f"{comp:.6f}",
                star,
            ])

    print(f"[Stage5] 完了: {output_path} に {len(results)} 件書き出しました")

    # ---- サマリ ----
    from collections import Counter
    dist = Counter(star for *_, star in results)
    print("[Stage5] 星レーティング分布:")
    for s in range(5):
        count = dist.get(s, 0)
        bar = "█" * (count * 40 // max(total, 1))
        print(f"  ★{s}: {count:4d}件  {bar}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage5: CLIP バッチスコアリング → 星レーティング判定（複合スコア / 画像-画像モード対応）"
    )
    # jpeg_dir は位置引数（省略時は --jpeg-dir で指定可）
    parser.add_argument("jpeg_dir_pos", nargs="?", default=None, metavar="jpeg_dir",
                        help="JPEG ディレクトリのパス（位置引数）")
    parser.add_argument("--jpeg-dir", default=None, help="JPEG ディレクトリのパス（オプション形式）")
    parser.add_argument("--profile", required=True, help="aesthetic_profile.json のパス")
    parser.add_argument("--output", required=True, help="出力 CSV パス")
    parser.add_argument(
        "--mode",
        choices=["text", "image"],
        default="text",
        help="スコアリングモード: text（テキストCLIP）または image（画像-画像CLIP）",
    )
    parser.add_argument(
        "--rated-samples",
        default=None,
        help="rated_samples.json のパス（--mode image 時に使用、省略時は --profile と同ディレクトリを探す）",
    )
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help='星変換の閾値をカンマ区切りで指定 (デフォルト: "0.25,0.45,0.60,0.75")',
    )
    parser.add_argument(
        "--weights",
        default=",".join(str(w) for w in DEFAULT_WEIGHTS),
        help='複合スコアの重み clip,high,low (デフォルト: "0.5,0.3,0.2")',
    )
    parser.add_argument(
        "--no-composite",
        action="store_true",
        help="複合スコアを無効化し clip_query 1本で採点（従来モード）",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="composite スコアの min-max 正規化を無効化（生クランプ値で star 変換）",
    )
    parser.add_argument("--verbose", action="store_true", help="全ファイルのスコアを表示")
    parser.add_argument("--demo", action="store_true",
                        help="デモモード: CLIPモデルのロードをスキップしサンプルスコアを生成する")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="CLIPモデル名 (例: ViT-B-32, ViT-B-16-SigLIP)")
    parser.add_argument("--pretrained", default=DEFAULT_PRETRAINED,
                        help="事前学習済み重み名 (例: openai, webli)")
    args = parser.parse_args()

    # jpeg_dir: 位置引数優先、次に --jpeg-dir
    jpeg_dir_str = args.jpeg_dir_pos or args.jpeg_dir
    if not jpeg_dir_str:
        print("ERROR: JPEG ディレクトリを位置引数または --jpeg-dir で指定してください", file=sys.stderr)
        sys.exit(1)

    profile_path = Path(args.profile)
    jpeg_dir = Path(jpeg_dir_str)
    output_path = Path(args.output)

    if not profile_path.exists():
        print(f"ERROR: プロファイルが見つかりません: {profile_path}", file=sys.stderr)
        sys.exit(1)
    if not jpeg_dir.is_dir():
        print(f"ERROR: ディレクトリが存在しません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    thresholds = [float(t) for t in args.thresholds.split(",")]
    weights = [float(w) for w in args.weights.split(",")]
    if len(weights) != 3:
        print("ERROR: --weights は 3 つの値が必要です (clip,high,low)", file=sys.stderr)
        sys.exit(1)

    if args.demo:
        import hashlib, time as _time
        print("[Stage5] デモモード: CLIPモデルのロードをスキップします")
        jpeg_files = sorted(
            list(jpeg_dir.glob("*.JPG")) + list(jpeg_dir.glob("*.jpg"))
        )
        print(f"[Stage5] 対象: {len(jpeg_files)} ファイル")
        results = []
        for p in jpeg_files:
            h = int(hashlib.md5(p.name.encode()).hexdigest()[:8], 16)
            clip_s = round(0.30 + (h % 50) / 100, 4)
            high_s = round(0.40 + ((h >> 4) % 40) / 100, 4)
            low_s  = round(0.10 + ((h >> 8) % 30) / 100, 4)
            comp   = round((clip_s * 0.5 + high_s * 0.3 + (1 - low_s) * 0.2), 4)
            star   = sum(1 for t in thresholds if comp >= t)
            results.append((p.name, clip_s, high_s, low_s, comp, star))
            _time.sleep(0.005)
        _write_csv_and_summary(output_path, results, len(results))
        return

    # open-clip-torch を自動インストール（stage1/.venv の pip を使用）
    script_dir = Path(__file__).parent
    venv_pip = script_dir.parent / "stage1" / ".venv" / "bin" / "pip"
    ensure_open_clip(str(venv_pip) if venv_pip.exists() else "pip")

    if args.mode == "image":
        # rated_samples.json の解決
        if args.rated_samples:
            rated_path = Path(args.rated_samples)
        else:
            rated_path = profile_path.parent / "rated_samples.json"
        if not rated_path.exists():
            print(f"ERROR: rated_samples.json が見つかりません: {rated_path}", file=sys.stderr)
            print("  --rated-samples オプションでパスを指定してください", file=sys.stderr)
            sys.exit(1)
        print(f"[Stage5] モード: 画像-画像 CLIP  (rated_samples: {rated_path})")
        run_image_mode(
            jpeg_dir,
            rated_path,
            output_path,
            thresholds,
            normalize=not args.no_normalize,
            verbose=args.verbose,
            model_name=args.model,
            pretrained=args.pretrained,
        )
    else:
        run(
            profile_path,
            jpeg_dir,
            output_path,
            thresholds,
            weights,
            use_composite=not args.no_composite,
            normalize=not args.no_normalize,
            verbose=args.verbose,
            model_name=args.model,
            pretrained=args.pretrained,
        )


if __name__ == "__main__":
    main()
