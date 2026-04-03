"""
threshold_analysis2.py — スコア逆転現象の詳細分析
★1-3 が ★4 よりスコアが高い問題を掘り下げる
"""

import csv
import re
from pathlib import Path

XMP_DIR = Path("/Users/daisuke/Pictures/ASA-test-data/v1.0.2/xmp_teacher")
BATCH_CSV = Path("/Users/daisuke/Pictures/ASA-test-data/v1.0.2/batch_scores.csv")
RATING_RE = re.compile(r'xmp:Rating[=\s>]+["\s]*(\d)')


def load_teacher_ratings(xmp_dir):
    ratings = {}
    for xmp_path in xmp_dir.glob("*.xmp"):
        stem = xmp_path.stem
        text = xmp_path.read_text(errors="replace")
        m = RATING_RE.search(text)
        rating = int(m.group(1)) if m else 0
        ratings[stem + ".JPG"] = rating
    return ratings


def load_batch_scores(csv_path):
    scores = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            scores[row["filename"]] = {
                "clip_score": float(row["clip_score"]),
                "high_score": float(row["high_score"]) if row["high_score"] else None,
                "low_score": float(row["low_score"]) if row["low_score"] else None,
                "composite_score": float(row["composite_score"]),
                "star_rating": int(row["star_rating"]),
            }
    return scores


def percentiles(data):
    s = sorted(data)
    n = len(s)
    def p(pct): return s[min(int(n * pct / 100), n-1)]
    return p(10), p(25), p(50), p(75), p(90)


teacher = load_teacher_ratings(XMP_DIR)
ai = load_batch_scores(BATCH_CSV)

merged = [
    {**ai[f], "teacher": teacher[f], "filename": f}
    for f in ai if f in teacher
]

# --- 各グループのスコア詳細 ---
print("=== composite_score 分布詳細 ===")
for label, pred in [("★4", lambda r: r["teacher"] == 4),
                    ("★3", lambda r: r["teacher"] == 3),
                    ("★2", lambda r: r["teacher"] == 2),
                    ("★1", lambda r: r["teacher"] == 1),
                    ("★0/なし", lambda r: r["teacher"] == 0)]:
    g = [r["composite_score"] for r in merged if pred(r)]
    if not g:
        print(f"  {label}: 0件")
        continue
    p10, p25, p50, p75, p90 = percentiles(g)
    mean = sum(g) / len(g)
    print(f"  {label} (n={len(g):3d}): mean={mean:.3f}  p10={p10:.3f}  p25={p25:.3f}  "
          f"p50={p50:.3f}  p75={p75:.3f}  p90={p90:.3f}  min={min(g):.3f}  max={max(g):.3f}")

# --- clip_score だけで見た場合 ---
print("\n=== clip_score 分布（composite の内訳）===")
for label, pred in [("★4", lambda r: r["teacher"] == 4),
                    ("★1-3", lambda r: 1 <= r["teacher"] <= 3),
                    ("★0/なし", lambda r: r["teacher"] == 0)]:
    g_clip = [r["clip_score"] for r in merged if pred(r)]
    g_high = [r["high_score"] for r in merged if pred(r) and r["high_score"] is not None]
    if not g_clip:
        continue
    print(f"  {label} clip_score: mean={sum(g_clip)/len(g_clip):.3f}  "
          f"high_score: {'mean='+f'{sum(g_high)/len(g_high):.3f}' if g_high else 'N/A'}")

# --- ★4 でスコアが低い例 TOP10 ---
print("\n=== ★4 なのに composite_score が低い例（下位10件）===")
star4_sorted = sorted([r for r in merged if r["teacher"] == 4], key=lambda r: r["composite_score"])
print(f"  {'filename':<20} {'composite':>9}  {'clip':>7}  {'ai_star':>7}")
for r in star4_sorted[:10]:
    print(f"  {r['filename']:<20} {r['composite_score']:>9.4f}  {r['clip_score']:>7.4f}  {r['star_rating']:>7}")

# --- ★1-3 でスコアが高い例 TOP10 ---
print("\n=== ★1-3 なのに composite_score が高い例（上位10件）===")
low_high = sorted([r for r in merged if 1 <= r["teacher"] <= 3], key=lambda r: -r["composite_score"])
print(f"  {'filename':<20} {'composite':>9}  {'clip':>7}  {'ai_star':>7}  {'teacher':>7}")
for r in low_high[:10]:
    print(f"  {r['filename']:<20} {r['composite_score']:>9.4f}  {r['clip_score']:>7.4f}  {r['star_rating']:>7}  {r['teacher']:>7}")

# --- AI★評価 vs 教師評価のクロス集計 ---
print("\n=== クロス集計: AI star_rating × 教師Rating ===")
print(f"  {'教師\\AI':>8}", end="")
for s in range(1, 5):
    print(f"  AI★{s}", end="")
print(f"  {'合計':>6}")

for t in [4, "1-3", 0]:
    if t == 4:
        rows = [r for r in merged if r["teacher"] == 4]
        label = "教師★4"
    elif t == "1-3":
        rows = [r for r in merged if 1 <= r["teacher"] <= 3]
        label = "教師★1-3"
    else:
        rows = [r for r in merged if r["teacher"] == 0]
        label = "教師★0"
    print(f"  {label:>8}", end="")
    for s in range(1, 5):
        cnt = sum(1 for r in rows if r["star_rating"] == s)
        print(f"  {cnt:>4}", end="")
    print(f"  {len(rows):>6}")

# --- Precision/Recall 曲線の主要点 ---
print("\n=== Precision-Recall 主要点（0.1刻み）===")
total_pos = sum(1 for r in merged if r["teacher"] == 4)
print(f"  {'thr':>5}  {'prec':>7}  {'rec':>7}  {'F1':>7}  {'selected':>8}  {'TP':>4}")
for i in range(0, 11):
    thr = i / 10
    tp = sum(1 for r in merged if r["teacher"] == 4 and r["composite_score"] >= thr)
    fp = sum(1 for r in merged if r["teacher"] != 4 and r["composite_score"] >= thr)
    fn = total_pos - tp
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / total_pos if total_pos > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    print(f"  {thr:>5.1f}  {prec:>7.4f}  {rec:>7.4f}  {f1:>7.4f}  {tp+fp:>8}  {tp:>4}")
