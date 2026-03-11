#!/usr/bin/env python3
"""
Stage 3: グループ代表カットの A/B 判定 → スタイルルール動的更新

処理フロー:
  1. stage2_groups.csv からグループを読み込む
  2. 各グループの代表 2 枚（first / last、またはサイズ上位 2 枚）を選出
  3. Claude API（マルチモーダル）で画像ペアを分析
  4. ユーザーが A/B を選択 → フィードバックを蓄積
  5. N 回の比較後、スタイルルールを更新・表示
  6. 結果を JSON に保存

使い方:
  python judge.py <jpeg_dir> [オプション]

例:
  python judge.py /path/to/S2_JPEG/ --csv stage2_groups.csv --rounds 10
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import anthropic

# ---- 定数 ----
MODEL = "claude-opus-4-6"
MAX_IMAGE_PX = 1568          # Claude Vision の推奨上限（長辺）


# ---- データ ----

@dataclass
class Comparison:
    group_id: int
    shot_a: str
    shot_b: str
    winner: str      # "A" / "B" / "skip" / "both_bad"
    reason: str      # Claude の分析テキスト（要約）
    timestamp: str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class StyleRules:
    summary: str = ""
    rules: list[str] = field(default_factory=list)
    updated_at: str = ""


# ---- 画像ユーティリティ ----

def _encode_image(path: Path) -> tuple[str, str]:
    """JPEG を base64 エンコードして (data, media_type) を返す。"""
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, "image/jpeg"


def _open_images(*paths: Path) -> None:
    """macOS Preview で画像を開く（バックグラウンド）。"""
    try:
        subprocess.Popen(
            ["open", "-a", "Preview"] + [str(p) for p in paths],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)  # Preview が起動するまで少し待つ
    except Exception:
        pass  # プレビュー失敗は無視


# ---- グループ選出 ----

def load_groups(csv_path: Path, jpeg_dir: Path) -> list[dict]:
    """stage2_groups.csv を読み込み、グループごとに代表 2 枚を選出する。

    Returns:
        list of {group_id, shot_a_path, shot_b_path, group_size}
    """
    import csv

    by_group: dict[int, list[dict]] = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gid = int(row["group_id"])
            by_group.setdefault(gid, []).append(row)

    candidates = []
    for gid, members in sorted(by_group.items()):
        if len(members) < 2:
            continue  # SOLO はスキップ

        # first / last を優先。どちらもなければ先頭 2 枚
        firsts = [m for m in members if m["position"] == "first"]
        lasts  = [m for m in members if m["position"] == "last"]
        if firsts and lasts:
            shot_a = firsts[0]
            shot_b = lasts[0]
        else:
            shot_a, shot_b = members[0], members[-1]

        path_a = jpeg_dir / shot_a["file"]
        path_b = jpeg_dir / shot_b["file"]
        if not path_a.exists() or not path_b.exists():
            continue

        candidates.append({
            "group_id":   gid,
            "group_size": len(members),
            "shot_a":     shot_a["file"],
            "shot_b":     shot_b["file"],
            "path_a":     path_a,
            "path_b":     path_b,
        })

    return candidates


# ---- Claude API ----

def analyze_pair(
    client: anthropic.Anthropic,
    path_a: Path,
    path_b: Path,
    style_rules: StyleRules,
) -> str:
    """2 枚の画像をマルチモーダルで分析し、差異と推奨をストリーミング出力する。"""

    data_a, media_a = _encode_image(path_a)
    data_b, media_b = _encode_image(path_b)

    rules_text = ""
    if style_rules.rules:
        rules_text = "\n\n【現在のスタイルルール】\n" + "\n".join(
            f"- {r}" for r in style_rules.rules
        )

    system = (
        "あなたはプロフォトグラファーのアシスタントです。"
        "2 枚の写真（A と B）を比較し、技術的・美的観点から違いを分析してください。"
        "観点例: ピント・ブレ・露出・表情・構図・自然さ・瞬間のクオリティ。"
        "分析は日本語で、簡潔に 200 字以内でまとめてください。"
        "最後に「→ 推奨: A / B / 差なし」の形式で1行追加してください。"
        + rules_text
    )

    print("\n[Claude が分析中...]", flush=True)

    result = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=512,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_a, "data": data_a},
                },
                {"type": "text", "text": "【写真 A】"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_b, "data": data_b},
                },
                {"type": "text", "text": "【写真 B】\nこの 2 枚を比較してください。"},
            ],
        }],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            result.append(text)

    print()
    return "".join(result)


def update_style_rules(
    client: anthropic.Anthropic,
    comparisons: list[Comparison],
    current_rules: StyleRules,
) -> StyleRules:
    """蓄積したフィードバックからスタイルルールを更新する。"""

    feedback_text = "\n".join(
        f"グループ{c.group_id}: {c.shot_a} vs {c.shot_b} → 選択={c.winner} / 分析={c.reason}"
        for c in comparisons
    )
    current_rules_text = "\n".join(f"- {r}" for r in current_rules.rules) or "（まだルールなし）"

    print("\n[スタイルルールを更新中...]", flush=True)

    with client.messages.stream(
        model=MODEL,
        max_tokens=1024,
        system=(
            "あなたはプロフォトグラファーの審美眼を学習するAIアシスタントです。"
            "ユーザーの A/B 選択履歴を分析し、このフォトグラファーの好みのパターンを"
            "簡潔な日本語ルールとして 3〜7 個に整理してください。"
            "形式: JSON { \"summary\": \"...\", \"rules\": [\"...\", ...] }"
        ),
        messages=[{
            "role": "user",
            "content": (
                f"【現在のルール】\n{current_rules_text}\n\n"
                f"【新しいフィードバック】\n{feedback_text}\n\n"
                "ルールを更新してください（JSON のみ出力）。"
            ),
        }],
    ) as stream:
        raw = "".join(stream.text_stream)

    # JSON を抽出
    try:
        # コードブロック除去
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())
        rules = StyleRules(
            summary=parsed.get("summary", ""),
            rules=parsed.get("rules", []),
            updated_at=datetime.now().isoformat(),
        )
        print("\n📋 スタイルルール更新:")
        print(f"  {rules.summary}")
        for r in rules.rules:
            print(f"  ・{r}")
        return rules
    except Exception as e:
        print(f"\n(ルール更新失敗: {e})")
        return current_rules


# ---- レーティング計算 ----

def compute_ratings(
    comparisons: list[Comparison],
    csv_path: Path,
) -> list[dict]:
    """A/B 判定結果を全ショットのレーティングスコアに変換する。

    スコア設計（0.0〜5.0）:
      base = bonus_weight（Stage 2 由来: 1.0〜1.5）
      A/B 勝者   : base × 2.5  → ~3.5〜3.75（★3〜4 相当）
      A/B 敗者   : base × 1.5  → ~1.5〜2.25（★1〜2 相当）
      both_bad   : base × 0.5  → 却下候補
      middle     : base × 1.8  → ~1.8〜2.7（★2 相当、Stage4で精査）
      solo       : base × 2.0  → ~2.0〜3.0（★2〜3 相当、Stage4で精査）
      skip(未判定): base × 2.0 → solo と同扱い

    Returns:
        list of {file, group_id, position, bonus_weight,
                 stage3_verdict, rating_score, rating_stars}
    """
    import csv as _csv

    # Stage2 CSV を全読み込み
    all_shots: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            all_shots.append({
                "file":         row["file"],
                "group_id":     int(row["group_id"]),
                "position":     row["position"],
                "bonus_weight": float(row["bonus_weight"]),
                "datetime":     row["datetime"],
            })

    # 判定結果をグループID→(winner_file, loser_file, verdict)でマップ
    verdict_map: dict[int, dict] = {}
    for c in comparisons:
        if c.winner == "A":
            verdict_map[c.group_id] = {"winner": c.shot_a, "loser": c.shot_b, "result": "judged"}
        elif c.winner == "B":
            verdict_map[c.group_id] = {"winner": c.shot_b, "loser": c.shot_a, "result": "judged"}
        elif c.winner == "both_bad":
            verdict_map[c.group_id] = {"winner": None, "loser": None, "result": "both_bad"}
        # "skip" は verdict_map に入れない（未判定扱い）

    results = []
    for shot in all_shots:
        gid      = shot["group_id"]
        pos      = shot["position"]
        bw       = shot["bonus_weight"]
        fname    = shot["file"]
        verdict  = "pending"

        if pos == "solo":
            score   = bw * 2.0
            verdict = "solo"
        elif gid not in verdict_map:
            # 未判定グループ（skip or rounds 未到達）
            score   = bw * 2.0
            verdict = "pending"
        else:
            vm = verdict_map[gid]
            if vm["result"] == "both_bad":
                score   = bw * 0.5
                verdict = "both_bad"
            elif pos in ("first", "last"):
                if fname == vm["winner"]:
                    score   = bw * 2.5
                    verdict = "winner"
                else:
                    score   = bw * 1.5
                    verdict = "loser"
            else:
                # middle: グループが判定済みでも個別スコアは Stage4 に委ねる
                score   = bw * 1.8
                verdict = "middle_pending"

        stars = min(5, max(0, round(score)))
        results.append({
            "file":          fname,
            "group_id":      gid,
            "position":      pos,
            "bonus_weight":  bw,
            "stage3_verdict": verdict,
            "rating_score":  round(score, 3),
            "rating_stars":  stars,
            "datetime":      shot["datetime"],
        })

    return sorted(results, key=lambda r: (r["group_id"], r["datetime"], r["file"]))


def print_dry_run(candidates: list[dict], csv_path: Path) -> None:
    """--dry-run: API を呼ばずにペア選出とレーティング設計を表示する。"""
    import csv as _csv

    # Stage2 全データ
    all_shots: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            all_shots.append(row)

    print("\n━━━  A/B ペア選出ロジック（dry-run）  ━━━\n")
    print(f"{'GID':>4}  {'枚':>3}  {'A = first':28}  {'B = last':28}  {'middle':>6}")
    print("─" * 78)
    for c in candidates:
        mid = c["group_size"] - 2
        print(
            f"{c['group_id']:>4}  {c['group_size']:>3}枚  "
            f"{c['shot_a']:28}  {c['shot_b']:28}  +{mid}枚"
        )

    print(f"\n→ 比較対象: {len(candidates)} グループ")
    print("→ 選出規則: 各グループの position=first を A、position=last を B に割り当て")
    print("            （first/last が存在しない場合は時刻順の先頭/末尾）\n")

    print("━━━  レーティング設計（仮: 全グループ A 勝利と仮定）  ━━━\n")
    print(f"{'verdict':>14}  {'乗数':>5}  {'スコア範囲':>14}  {'★変換':>6}  {'説明'}")
    print("─" * 70)
    rows_design = [
        ("winner",         2.5, "1.0×2.5〜1.5×2.5", "★3〜4", "A/B の勝者"),
        ("loser",          1.5, "1.0×1.5〜1.5×1.5", "★2〜3", "A/B の敗者"),
        ("both_bad",       0.5, "1.0×0.5〜1.5×0.5", "★1",   "両方却下"),
        ("middle_pending", 1.8, "1.0×1.8〜1.5×1.8", "★2〜3", "中間カット（Stage4で精査）"),
        ("solo",           2.0, "1.5×2.0=3.0",      "★3",   "SOLOショット（Stage4）"),
        ("pending",        2.0, "—",                 "★2〜3", "未判定グループ"),
    ]
    for v, mult, rng, stars, desc in rows_design:
        print(f"{v:>14}  ×{mult:<4}  {rng:>14}  {stars:>6}  {desc}")

    # 全 A 勝利シミュレーション
    sim_comparisons = [
        Comparison(
            group_id=c["group_id"],
            shot_a=c["shot_a"],
            shot_b=c["shot_b"],
            winner="A",
            reason="(dry-run simulation)",
        )
        for c in candidates
    ]
    ratings = compute_ratings(sim_comparisons, csv_path)

    star_dist = {}
    for r in ratings:
        s = r["rating_stars"]
        star_dist[s] = star_dist.get(s, 0) + 1

    verdict_dist: dict[str, int] = {}
    for r in ratings:
        v = r["stage3_verdict"]
        verdict_dist[v] = verdict_dist.get(v, 0) + 1

    print("\n━━━  シミュレーション結果（全 A 勝利と仮定 / 330枚）  ━━━\n")
    print("verdict 分布:")
    for v, n in sorted(verdict_dist.items()):
        print(f"  {v:>16}: {n:>3}枚")
    print("\n★ 分布:")
    for s in sorted(star_dist.keys()):
        bar = "█" * star_dist[s]
        print(f"  ★{s}: {star_dist[s]:>3}枚  {bar}")

    print("\n━━━  実際の動作フロー  ━━━\n")
    print("  [A/B判定 N回]")
    print("    ↓  judge.py がユーザー入力を収集")
    print("    ↓  N回ごとに Claude でスタイルルール抽出")
    print("    ↓  stage3_results.json に蓄積")
    print("")
    print("  [レーティング計算 = compute_ratings()]")
    print("    ↓  winner → bonus_weight × 2.5 → ★3〜4")
    print("    ↓  loser  → bonus_weight × 1.5 → ★1〜2")
    print("    ↓  middle → bonus_weight × 1.8 → ★2〜3（Stage4 LLM 精査待ち）")
    print("    ↓  solo   → bonus_weight × 2.0 → ★3（Stage4 LLM 精査待ち）")
    print("")
    print("  [Stage 4（未実装）]")
    print("    → middle/solo/pending を Claude がスタイルルールに基づきバッチ評価")
    print("    → XMP Sidecar 書き出し → Lightroom に取り込み")


# ---- 対話ループ ----

def run_session(
    client: anthropic.Anthropic,
    candidates: list[dict],
    rounds: int,
    output_path: Path,
    update_every: int,
) -> None:
    """メイン対話セッション。"""

    # 既存セッションのロード
    comparisons: list[Comparison] = []
    style_rules = StyleRules()

    if output_path.exists():
        try:
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            comparisons = [Comparison(**c) for c in saved.get("comparisons", [])]
            sr = saved.get("style_rules", {})
            style_rules = StyleRules(**sr) if sr else StyleRules()
            done_groups = {c.group_id for c in comparisons}
            candidates = [c for c in candidates if c["group_id"] not in done_groups]
            print(f"前回セッションを継続: {len(comparisons)} 件完了済み、残り {len(candidates)} グループ")
        except Exception:
            pass

    if not candidates:
        print("比較するグループがありません。")
        return

    total = min(rounds, len(candidates))
    print(f"\n=== Stage 3: A/B 判定セッション ({total} 回) ===")
    print("コマンド: A / B / s (スキップ) / x (終了)\n")

    for i, cand in enumerate(candidates[:total]):
        gid        = cand["group_id"]
        group_size = cand["group_size"]
        path_a     = cand["path_a"]
        path_b     = cand["path_b"]

        print(f"─── [{i + 1}/{total}] グループ {gid}（{group_size} 枚）───")
        print(f"  A: {cand['shot_a']}")
        print(f"  B: {cand['shot_b']}")

        # Preview で画像を開く
        _open_images(path_a, path_b)

        # Claude 分析
        analysis = analyze_pair(client, path_a, path_b, style_rules)

        # ユーザー入力
        while True:
            raw = input("\n  あなたの選択 [A/B/s=skip/x=終了]: ").strip().lower()
            if raw in ("a", "b", "s", "skip", "x", "exit", "quit"):
                break
            print("  A / B / s / x で入力してください。")

        if raw in ("x", "exit", "quit"):
            print("セッションを終了します。")
            break

        winner = "A" if raw == "a" else "B" if raw == "b" else "skip"
        comp = Comparison(
            group_id=gid,
            shot_a=cand["shot_a"],
            shot_b=cand["shot_b"],
            winner=winner,
            reason=analysis[:300],  # 保存用に切り詰め
        )
        comparisons.append(comp)

        # 定期的にスタイルルールを更新
        judged = [c for c in comparisons if c.winner in ("A", "B")]
        if len(judged) > 0 and len(judged) % update_every == 0:
            style_rules = update_style_rules(client, judged, style_rules)

        # 途中保存
        _save(output_path, comparisons, style_rules)
        print()

    # セッション終了後に最終ルール更新
    judged = [c for c in comparisons if c.winner in ("A", "B")]
    if judged:
        style_rules = update_style_rules(client, judged, style_rules)

    _save(output_path, comparisons, style_rules)
    print(f"\n✅ 完了。結果を保存: {output_path}")
    print(f"   比較数: {len(comparisons)} / 有効判定: {len(judged)}")


def _save(path: Path, comparisons: list[Comparison], rules: StyleRules) -> None:
    data = {
        "comparisons": [asdict(c) for c in comparisons],
        "style_rules": asdict(rules),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- エントリポイント ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 3: A/B 判定でスタイルルールを学習",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("jpeg_dir",       help="JPEG ディレクトリ")
    parser.add_argument("--csv",          default="stage2_groups.csv",
                        help="Stage 2 出力 CSV")
    parser.add_argument("--output",       default="stage3_results.json",
                        help="結果 JSON ファイル")
    parser.add_argument("--rounds",       type=int, default=10,
                        help="比較回数")
    parser.add_argument("--update-every", type=int, default=3,
                        help="N 回判定ごとにスタイルルールを更新")
    parser.add_argument("--api-key",      default=None,
                        help="Anthropic API キー（未指定時は ANTHROPIC_API_KEY 環境変数を使用）")
    parser.add_argument("--dry-run",      action="store_true",
                        help="API を呼ばずにペア選出・レーティング設計をシミュレーション表示")
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.exists():
        print(f"エラー: {jpeg_dir}")
        sys.exit(1)

    # CSV パスを解決（絶対パス or jpeg_dir 親ディレクトリ相対）
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = jpeg_dir.parent / csv_path
    if not csv_path.exists():
        print(f"エラー: CSV が見つかりません: {csv_path}")
        sys.exit(1)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = jpeg_dir.parent / output_path

    # dry-run は API キー不要
    if args.dry_run:
        print(f"\n=== Aesthetic Shadowing Agent - Stage 3 [DRY-RUN] ===")
        print(f"JPEG: {jpeg_dir}")
        print(f"CSV:  {csv_path}")
        candidates = load_groups(csv_path, jpeg_dir)
        print(f"比較可能グループ数: {len(candidates)}\n")
        print_dry_run(candidates, csv_path)
        return

    # API キーの解決（引数 > 環境変数）— ヘッダー出力より先にチェック
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "エラー: Anthropic API キーが見つかりません。\n"
            "以下のいずれかで指定してください:\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  python judge.py ... --api-key sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n=== Aesthetic Shadowing Agent - Stage 3 ===")
    print(f"JPEG: {jpeg_dir}")
    print(f"CSV:  {csv_path}")

    candidates = load_groups(csv_path, jpeg_dir)
    print(f"比較可能グループ数: {len(candidates)}")

    if not candidates:
        print("比較できるグループがありません（全グループが SOLO）。")
        sys.exit(0)

    client = anthropic.Anthropic(api_key=api_key)

    run_session(
        client=client,
        candidates=candidates,
        rounds=args.rounds,
        output_path=output_path,
        update_every=args.update_every,
    )


if __name__ == "__main__":
    main()
