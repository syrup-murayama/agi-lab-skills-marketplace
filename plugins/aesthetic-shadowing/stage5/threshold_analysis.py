"""
threshold_analysis.py — 教師データ vs AIスコア しきい値分析
★4検出F1を最大化する composite_score しきい値を求める
"""

import csv
import re
import os
import sys
from pathlib import Path

XMP_DIR = Path("/Users/daisuke/Pictures/ASA-test-data/v1.0.2/xmp_teacher")
BATCH_CSV = Path("/Users/daisuke/Pictures/ASA-test-data/v1.0.2/batch_scores.csv")
RATING_RE = re.compile(r'xmp:Rating[=\s>]+["\s]*(\d)')


def load_teacher_ratings(xmp_dir: Path) -> dict[str, int]:
    """XMPから教師ラベルを読み込む。Ratingなし = 0"""
    ratings = {}
    for xmp_path in xmp_dir.glob("*.xmp"):
        stem = xmp_path.stem  # e.g. _A6A6912
        text = xmp_path.read_text(errors="replace")
        m = RATING_RE.search(text)
        rating = int(m.group(1)) if m else 0
        # JPEGファイル名に対応: _A6A6912.JPG
        jpg_name = stem + ".JPG"
        ratings[jpg_name] = rating
    return ratings


def load_batch_scores(csv_path: Path) -> dict[str, dict]:
    """batch_scores.csv を読み込む"""
    scores = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scores[row["filename"]] = {
                "composite_score": float(row["composite_score"]),
                "star_rating": int(row["star_rating"]),
            }
    return scores


def group_label(rating: int) -> str:
    if rating == 4:
        return "★4"
    elif rating >= 1:
        return "★1-3"
    else:
        return "★0/なし"


def percentile(data, p):
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def main():
    print("=== データ読み込み ===")
    teacher = load_teacher_ratings(XMP_DIR)
    ai = load_batch_scores(BATCH_CSV)

    # マッチングして結合
    merged = []
    unmatched_teacher = 0
    unmatched_ai = 0

    for fname, score_data in ai.items():
        if fname in teacher:
            merged.append({
                "filename": fname,
                "composite_score": score_data["composite_score"],
                "ai_star": score_data["star_rating"],
                "teacher_rating": teacher[fname],
            })
        else:
            unmatched_ai += 1

    for fname in teacher:
        if fname not in ai:
            unmatched_teacher += 1

    print(f"  AI スコア: {len(ai)}件")
    print(f"  教師XMP: {len(teacher)}件")
    print(f"  マッチ: {len(merged)}件  (AI未マッチ={unmatched_ai}, 教師未マッチ={unmatched_teacher})")

    # --- 1. composite_score 分布 by 教師ラベル ---
    print("\n=== 1. composite_score 分布（教師ラベル別） ===")
    groups = {"★4": [], "★1-3": [], "★0/なし": []}
    for row in merged:
        g = group_label(row["teacher_rating"])
        groups[g].append(row["composite_score"])

    for g, scores in groups.items():
        if not scores:
            print(f"  {g}: データなし")
            continue
        mean = sum(scores) / len(scores)
        median = percentile(scores, 50)
        p25 = percentile(scores, 25)
        p75 = percentile(scores, 75)
        mn = min(scores)
        mx = max(scores)
        print(f"  {g} (n={len(scores):4d}): mean={mean:.4f}  median={median:.4f}  "
              f"p25={p25:.4f}  p75={p75:.4f}  min={mn:.4f}  max={mx:.4f}")

    # --- 2. F1最大化しきい値探索 ---
    print("\n=== 2. F1最大化しきい値探索（★4検出）===")
    print("  Positive = 教師★4, Negative = 教師★0〜3")
    print("  予測Positive = composite_score >= threshold")

    positives = [r for r in merged if r["teacher_rating"] == 4]
    negatives = [r for r in merged if r["teacher_rating"] != 4]
    total_pos = len(positives)
    print(f"  教師★4={total_pos}件  教師★0-3={len(negatives)}件")

    best = {"threshold": 0.0, "f1": 0.0, "precision": 0.0, "recall": 0.0, "tp": 0, "fp": 0, "fn": 0}
    results = []

    for i in range(101):
        thr = i / 100.0
        tp = sum(1 for r in positives if r["composite_score"] >= thr)
        fp = sum(1 for r in negatives if r["composite_score"] >= thr)
        fn = total_pos - tp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / total_pos if total_pos > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        results.append((thr, precision, recall, f1, tp, fp, fn))

        if f1 > best["f1"]:
            best = {"threshold": thr, "f1": f1, "precision": precision,
                    "recall": recall, "tp": tp, "fp": fp, "fn": fn}

    # 上位5件表示
    top5 = sorted(results, key=lambda x: -x[3])[:5]
    print(f"\n  上位5しきい値候補:")
    print(f"  {'thr':>5}  {'precision':>9}  {'recall':>7}  {'F1':>7}  {'TP':>4}  {'FP':>4}  {'FN':>4}")
    for r in top5:
        thr, prec, rec, f1, tp, fp, fn = r
        print(f"  {thr:>5.2f}  {prec:>9.4f}  {rec:>7.4f}  {f1:>7.4f}  {tp:>4}  {fp:>4}  {fn:>4}")

    # --- 3. 現在のしきい値（AI★3境界）との比較 ---
    print("\n=== 3. 現在のしきい値との比較 ===")

    # 現在: AI star_rating >= 3 を採用候補とする
    tp_cur = sum(1 for r in merged if r["teacher_rating"] == 4 and r["ai_star"] >= 3)
    fp_cur = sum(1 for r in merged if r["teacher_rating"] != 4 and r["ai_star"] >= 3)
    fn_cur = sum(1 for r in merged if r["teacher_rating"] == 4 and r["ai_star"] < 3)
    prec_cur = tp_cur / (tp_cur + fp_cur) if (tp_cur + fp_cur) > 0 else 0.0
    rec_cur = tp_cur / total_pos if total_pos > 0 else 0.0
    f1_cur = 2 * prec_cur * rec_cur / (prec_cur + rec_cur) if (prec_cur + rec_cur) > 0 else 0.0

    print(f"  現在 (AI star>=3):      precision={prec_cur:.4f}  recall={rec_cur:.4f}  F1={f1_cur:.4f}  TP={tp_cur}  FP={fp_cur}  FN={fn_cur}")
    print(f"  最適 (thr={best['threshold']:.2f}): precision={best['precision']:.4f}  recall={best['recall']:.4f}  F1={best['f1']:.4f}  TP={best['tp']}  FP={best['fp']}  FN={best['fn']}")
    print(f"\n  F1改善: {f1_cur:.4f} → {best['f1']:.4f} (+{best['f1']-f1_cur:.4f})")

    # 現在の composite_score しきい値も確認（AI star>=3 の実際の最低スコア）
    star3_scores = [r["composite_score"] for r in merged if r["ai_star"] >= 3]
    if star3_scores:
        cur_thr_actual = min(star3_scores)
        print(f"  現在 AI★3以上の composite_score 最低値: {cur_thr_actual:.4f}")

    # --- 提案 ---
    print("\n=== 4. score.py --thresholds 推奨値 ===")
    print(f"  最適F1しきい値: {best['threshold']:.2f}")
    # --thresholds は [★1境界, ★2境界, ★3境界] の3値を想定
    # ★4 = composite_score >= best_thr として運用するなら ★3境界 = best_thr
    # 現在のデフォルトが [0.3, 0.5, 0.7] 等の場合を想定
    print(f"\n  推奨: --thresholds <★1境界> <★2境界> {best['threshold']:.2f}")
    print(f"  ※ ★3以上を「採用候補」、{best['threshold']:.2f}以上を「★4候補（クライアント納品）」として使う")

    # batch_scores_v2.csv でも同じ分析
    print("\n=== 付録: batch_scores_v2.csv との比較 ===")
    ai_v2 = load_batch_scores(Path("/Users/daisuke/Pictures/ASA-test-data/v1.0.2/batch_scores_v2.csv"))
    merged_v2 = []
    for fname, score_data in ai_v2.items():
        if fname in teacher:
            merged_v2.append({
                "composite_score": score_data["composite_score"],
                "teacher_rating": teacher[fname],
            })
    total_pos_v2 = sum(1 for r in merged_v2 if r["teacher_rating"] == 4)
    best_v2 = {"threshold": 0.0, "f1": 0.0}
    for i in range(101):
        thr = i / 100.0
        tp = sum(1 for r in merged_v2 if r["teacher_rating"] == 4 and r["composite_score"] >= thr)
        fp = sum(1 for r in merged_v2 if r["teacher_rating"] != 4 and r["composite_score"] >= thr)
        fn = total_pos_v2 - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / total_pos_v2 if total_pos_v2 > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        if f1 > best_v2["f1"]:
            best_v2 = {"threshold": thr, "f1": f1, "precision": prec, "recall": rec, "tp": tp, "fp": fp, "fn": fn}
    print(f"  v2最適 (thr={best_v2['threshold']:.2f}): precision={best_v2['precision']:.4f}  recall={best_v2['recall']:.4f}  F1={best_v2['f1']:.4f}  TP={best_v2['tp']}  FP={best_v2['fp']}  FN={best_v2['fn']}")


if __name__ == "__main__":
    main()
