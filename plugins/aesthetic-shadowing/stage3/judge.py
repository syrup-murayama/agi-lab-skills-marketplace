#!/usr/bin/env python3
"""
Stage 3: グループ内トーナメント → ベストショット選出 + スタイルルール学習

設計:
  各グループで「King of the Hill」トーナメントを実施。
    champion = shots[0]
    shot[1] vs champion → 勝者が新 champion
    shot[2] vs champion → 勝者が新 champion
    ... → 最終 champion = グループ最良カット

  大グループ（> --max-per-group 枚）は先頭・末尾・均等サンプリングで
  代表を絞り込んでからトーナメント実施。

レーティング（compute_ratings）:
  champion            → score 5.0 → ★5
  runner-up           → score 4.0 → ★4（最終戦敗退）
  途中敗退（後半 50%） → score 3.0 → ★3
  途中敗退（前半 50%） → score 2.0 → ★2
  トーナメント未到達  → score 3.0 → ★3（Stage4 で精査）
  SOLO                → score 3.0 → ★3（Stage4 で精査）

使い方:
  python judge.py <jpeg_dir> [オプション]

例:
  python judge.py /path/to/S2_JPEG/ --csv stage2_groups.csv
  python judge.py /path/to/S2_JPEG/ --dry-run
"""

import argparse
import base64
import csv as _csv
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
DEFAULT_MAX_PER_GROUP = 8   # 大グループでサンプリングする上限枚数


# ---- データ構造 ----

@dataclass
class ShotRecord:
    """1ショットの情報（Stage2 CSV から）。"""
    file: str
    group_id: int
    position: str        # first / last / middle / solo
    bonus_weight: float
    person_count: int
    dt: str              # datetime (ISO)


@dataclass
class Judgment:
    """1回の A/B 判定結果。"""
    group_id: int
    round_num: int       # グループ内の何戦目か（0始まり）
    shot_a: str          # champion（挑戦される側）
    shot_b: str          # challenger（挑戦する側）
    winner: str          # "A" / "B" / "skip"
    analysis: str        # Claude の分析テキスト（要約）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class GroupTournament:
    """グループ単位のトーナメント状態。"""
    group_id: int
    shots: list[str]             # 比較順の file 名リスト
    champion: str = ""           # 現在の champion file
    defeated: list[dict] = field(default_factory=list)
    # [{file, round_eliminated, total_rounds}]
    round_num: int = 0           # 次の対戦番号
    next_idx: int = 1            # 次の challenger index
    done: bool = False

    def __post_init__(self):
        if self.shots and not self.champion:
            self.champion = self.shots[0]


@dataclass
class StyleRules:
    summary: str = ""
    rules: list[str] = field(default_factory=list)
    updated_at: str = ""


# ---- Stage2 CSV 読み込み ----

def load_all_shots(csv_path: Path) -> dict[int, list[ShotRecord]]:
    """stage2_groups.csv を全行読み込み、グループID → ショットリスト を返す。"""
    by_group: dict[int, list[ShotRecord]] = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            gid = int(row["group_id"])
            by_group.setdefault(gid, []).append(ShotRecord(
                file=row["file"],
                group_id=gid,
                position=row["position"],
                bonus_weight=float(row["bonus_weight"]),
                person_count=int(float(row["person_count"])),
                dt=row["datetime"],
            ))
    # 各グループを時刻順にソート
    for members in by_group.values():
        members.sort(key=lambda s: (s.dt, s.file))
    return by_group


def sample_shots(members: list[ShotRecord], max_n: int) -> list[ShotRecord]:
    """グループから代表ショットを最大 max_n 枚サンプリング。

    優先順位: first, last, 均等サンプリングした middle
    """
    if len(members) <= max_n:
        return members

    firsts  = [s for s in members if s.position == "first"]
    lasts   = [s for s in members if s.position == "last"]
    middles = [s for s in members if s.position == "middle"]

    selected = list(firsts) + list(lasts)
    remaining = max_n - len(selected)

    if remaining > 0 and middles:
        step = max(1, len(middles) // remaining)
        selected += middles[::step][:remaining]

    # 重複除去 → 時刻順
    seen = set()
    result = []
    for s in members:  # 元の時刻順を保持
        if s.file not in seen and s in selected:
            result.append(s)
            seen.add(s.file)

    return result[:max_n]


def build_tournaments(
    by_group: dict[int, list[ShotRecord]],
    max_per_group: int,
    jpeg_dir: Path,
) -> list[GroupTournament]:
    """グループごとにトーナメントオブジェクトを生成（SOLO グループは除外）。"""
    tournaments = []
    for gid, members in sorted(by_group.items()):
        if len(members) < 2:
            continue  # SOLO はスキップ

        sampled = sample_shots(members, max_per_group)
        # 画像ファイルが実在するものだけ
        valid = [s for s in sampled if (jpeg_dir / s.file).exists()]
        if len(valid) < 2:
            continue

        tournaments.append(GroupTournament(
            group_id=gid,
            shots=[s.file for s in valid],
            champion=valid[0].file,
        ))

    return tournaments


# ---- 画像ユーティリティ ----

def _encode(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _open_preview(*paths: Path) -> None:
    try:
        subprocess.Popen(
            ["open", "-a", "Preview"] + [str(p) for p in paths],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
    except Exception:
        pass


# ---- Claude 分析 ----

def analyze_pair(
    client: anthropic.Anthropic,
    path_a: Path,
    path_b: Path,
    style_rules: StyleRules,
    label_a: str = "A（現チャンピオン）",
    label_b: str = "B（挑戦者）",
) -> str:
    rules_text = ""
    if style_rules.rules:
        rules_text = "\n\n【現在のスタイルルール】\n" + "\n".join(
            f"- {r}" for r in style_rules.rules
        )

    system = (
        "あなたはプロフォトグラファーのアシスタントです。"
        "2 枚の写真を比較し、技術・美的観点から違いを 150 字以内で分析してください。"
        "観点例: ピント・ブレ・露出・表情・構図・瞬間の質。"
        "最後に「→ 推奨: A / B / 差なし」を 1 行で追加してください。"
        + rules_text
    )

    print(f"\n  [Claude 分析中...]", flush=True)
    result = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=400,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": _encode(path_a)}},
                {"type": "text", "text": f"【{label_a}】"},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": _encode(path_b)}},
                {"type": "text", "text": f"【{label_b}】\nこの 2 枚を比較してください。"},
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
    judgments: list[Judgment],
    current: StyleRules,
) -> StyleRules:
    feedback = "\n".join(
        f"G{j.group_id}-R{j.round_num}: {j.shot_a} vs {j.shot_b} "
        f"→ 勝者={j.winner} / {j.analysis[:80]}"
        for j in judgments if j.winner in ("A", "B")
    )
    cur_rules = "\n".join(f"- {r}" for r in current.rules) or "（まだルールなし）"

    print("\n  [スタイルルール更新中...]", flush=True)
    result = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=800,
        system=(
            "ユーザーの写真選択履歴からフォトグラファーの好みのパターンを"
            "3〜7 個の日本語ルールに整理してください。"
            "形式: JSON { \"summary\": \"...\", \"rules\": [\"...\", ...] }"
        ),
        messages=[{
            "role": "user",
            "content": (
                f"【現在のルール】\n{cur_rules}\n\n"
                f"【新しい判定履歴】\n{feedback}\n\n"
                "ルールを更新してください（JSON のみ）。"
            ),
        }],
    ) as stream:
        for text in stream.text_stream:
            result.append(text)

    raw = "".join(result)
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())
        updated = StyleRules(
            summary=parsed.get("summary", ""),
            rules=parsed.get("rules", []),
            updated_at=datetime.now().isoformat(),
        )
        print("\n  📋 スタイルルール更新:")
        print(f"    {updated.summary}")
        for r in updated.rules:
            print(f"    ・{r}")
        return updated
    except Exception as e:
        print(f"\n  (ルール更新失敗: {e})")
        return current


# ---- レーティング計算 ----

def compute_ratings(
    tournaments: list[GroupTournament],
    by_group: dict[int, list[ShotRecord]],
) -> list[dict]:
    """トーナメント結果を全ショットのスコアに変換する。

    トーナメント参加ショットのスコア:
      champion:             5.0
      runner-up:            4.0  （最終戦で負けた）
      敗退（後半 50%）:     3.0
      敗退（前半 50%）:     2.0

    トーナメント未参加:
      SOLO:                 3.0
      グループ内未サンプル: 3.0  （Stage4 で精査）
    """
    # トーナメント結果をファイル名でルックアップできるようにする
    # {file: score}
    score_map: dict[str, float] = {}

    for t in tournaments:
        total_rounds = len(t.shots) - 1
        if total_rounds == 0:
            continue

        # champion
        score_map[t.champion] = 5.0

        # defeated
        for d in t.defeated:
            r = d["round_eliminated"]
            # 後半か前半か（0始まり）
            if r >= total_rounds * 0.5:
                score_map[d["file"]] = 3.0
            else:
                score_map[d["file"]] = 2.0

        # runner-up = 最後に負けた shot
        if t.defeated:
            last_d = max(t.defeated, key=lambda d: d["round_eliminated"])
            if last_d["round_eliminated"] == total_rounds - 1:
                score_map[last_d["file"]] = 4.0

    # 全ショットにスコアを付与
    results = []
    for gid, members in sorted(by_group.items()):
        for shot in members:
            score = score_map.get(shot.file, 3.0)
            results.append({
                "file":          shot.file,
                "group_id":      gid,
                "position":      shot.position,
                "bonus_weight":  shot.bonus_weight,
                "rating_score":  score,
                "rating_stars":  min(5, max(1, round(score))),
                "datetime":      shot.dt,
            })

    return sorted(results, key=lambda r: (r["group_id"], r["datetime"], r["file"]))


# ---- dry-run 表示 ----

def print_dry_run(
    tournaments: list[GroupTournament],
    by_group: dict[int, list[ShotRecord]],
) -> None:
    total_rounds = sum(len(t.shots) - 1 for t in tournaments)
    total_shots  = sum(len(m) for m in by_group.values())
    solo_count   = sum(1 for m in by_group.values() if len(m) == 1)

    print(f"\n{'━'*60}")
    print(f"  グループ数: {len(by_group)}  /  総ショット: {total_shots}")
    print(f"  SOLO（除外）: {solo_count}  /  トーナメント対象: {len(tournaments)} グループ")
    print(f"  総対戦数: {total_rounds} 回")
    print(f"{'━'*60}\n")

    print(f"{'GID':>4}  {'元枚':>3}  {'対象':>3}  {'対戦':>3}  "
          f"{'shots（トーナメント順）'}")
    print("─" * 78)
    for t in tournaments:
        orig = len(by_group[t.group_id])
        n    = len(t.shots)
        rounds = n - 1
        shots_preview = " → ".join(s.replace(".JPG", "") for s in t.shots[:4])
        if n > 4:
            shots_preview += f" → ...（+{n-4}）"
        print(f"{t.group_id:>4}  {orig:>3}枚  {n:>3}枚  {rounds:>3}戦  {shots_preview}")

    print(f"\n  合計: {total_rounds} 対戦（1対戦あたり約 10〜20 秒）")
    print(f"  推定所要時間: {total_rounds * 15 // 60} 分 {total_rounds * 15 % 60} 秒\n")

    print("━━━  レーティング設計  ━━━\n")
    print(f"  {'トーナメント結果':>20}  {'スコア':>6}  {'★':>2}  説明")
    print("  " + "─" * 55)
    rows = [
        ("champion",           "5.0", "★5", "グループ最優秀カット"),
        ("runner-up",          "4.0", "★4", "最終戦で champion に敗退"),
        ("後半敗退（50%以降）", "3.0", "★3", "中盤まで勝ち残った"),
        ("前半敗退（50%未満）", "2.0", "★2", "早期敗退"),
        ("未サンプル / SOLO",  "3.0", "★3", "Stage4 LLM で個別精査"),
    ]
    for name, score, stars, desc in rows:
        print(f"  {name:>20}  {score:>6}  {stars:>2}  {desc}")

    # シミュレーション: 全勝 champion = shots[0] と仮定
    sim_tournaments = []
    for t in tournaments:
        st = GroupTournament(
            group_id=t.group_id,
            shots=t.shots,
            champion=t.shots[0],
            defeated=[
                {"file": s, "round_eliminated": i, "total_rounds": len(t.shots)-1}
                for i, s in enumerate(t.shots[1:])
            ],
            done=True,
        )
        sim_tournaments.append(st)

    ratings = compute_ratings(sim_tournaments, by_group)
    star_dist: dict[int, int] = {}
    for r in ratings:
        s = r["rating_stars"]
        star_dist[s] = star_dist.get(s, 0) + 1

    print(f"\n  シミュレーション結果（全グループ shots[0] が champion と仮定）")
    print(f"  {'★':>2}  {'枚数':>5}  棒グラフ")
    for s in sorted(star_dist.keys()):
        n = star_dist[s]
        bar = "█" * min(n, 50)
        print(f"  ★{s}  {n:>5}枚  {bar}")

    print(f"\n━━━  実際の動作フロー  ━━━\n")
    print("  各グループ: shots[0] が最初の champion")
    print("  shots[1] vs champion → 勝者が新 champion")
    print("  shots[2] vs champion → 勝者が新 champion  ...")
    print("  → 最終 champion = グループの最良カット = ★5")
    print()
    print("  N 戦ごとに Claude がスタイルルールを更新")
    print("  全トーナメント完了後 → compute_ratings() → stage3_results.json")
    print("  Stage4: middle/SOLO を style rules でバッチ評価 → XMP 書き出し")


# ---- 対話セッション ----

def run_session(
    client: anthropic.Anthropic,
    tournaments: list[GroupTournament],
    by_group: dict[int, list[ShotRecord]],
    jpeg_dir: Path,
    output_path: Path,
    update_every: int,
) -> None:
    judgments: list[Judgment] = []
    style_rules = StyleRules()

    # 前回セッション復元
    if output_path.exists():
        try:
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            judgments = [Judgment(**j) for j in saved.get("judgments", [])]
            sr = saved.get("style_rules", {})
            style_rules = StyleRules(**sr) if sr else StyleRules()

            # 復元: 完了済みトーナメントとラウンドを再現
            for j in judgments:
                t = next((t for t in tournaments if t.group_id == j.group_id), None)
                if not t:
                    continue
                if j.winner in ("A", "B"):
                    champ_wins = (j.winner == "A")
                    challenger = t.shots[t.next_idx] if t.next_idx < len(t.shots) else None
                    if challenger is None:
                        continue
                    if not champ_wins:
                        t.defeated.append({
                            "file": t.champion,
                            "round_eliminated": t.round_num,
                            "total_rounds": len(t.shots) - 1,
                        })
                        t.champion = challenger
                    else:
                        t.defeated.append({
                            "file": challenger,
                            "round_eliminated": t.round_num,
                            "total_rounds": len(t.shots) - 1,
                        })
                    t.round_num += 1
                    t.next_idx += 1
                elif j.winner == "skip":
                    t.next_idx += 1
                    t.round_num += 1
                if t.next_idx >= len(t.shots):
                    t.done = True

            n_done = sum(1 for t in tournaments if t.done)
            print(f"前回セッション復元: 判定 {len(judgments)} 件 / "
                  f"グループ完了 {n_done}/{len(tournaments)}")
        except Exception as e:
            print(f"(復元失敗: {e}、新規スタート)")

    pending = [t for t in tournaments if not t.done]
    if not pending:
        print("すべてのトーナメントが完了済みです。")
        _save(output_path, judgments, style_rules, tournaments, by_group)
        return

    total_remaining = sum(len(t.shots) - 1 - t.round_num for t in pending)
    print(f"\n=== Stage 3: トーナメント対戦セッション ===")
    print(f"残り対戦数: {total_remaining}  /  グループ: {len(pending)} 個")
    print("コマンド: A (champion 勝ち) / B (challenger 勝ち) / s (スキップ) / x (終了)\n")

    judged_count = 0

    for t in pending:
        orig_size = len(by_group[t.group_id])
        print(f"\n{'═'*60}")
        print(f"  グループ {t.group_id}（元 {orig_size} 枚 → トーナメント {len(t.shots)} 枚）")
        print(f"{'═'*60}")

        while not t.done:
            if t.next_idx >= len(t.shots):
                t.done = True
                break

            challenger = t.shots[t.next_idx]
            champion   = t.champion
            round_label = f"第 {t.round_num + 1} 戦 / {len(t.shots) - 1} 戦"

            print(f"\n  [{round_label}]")
            print(f"  A（champion） : {champion}")
            print(f"  B（challenger）: {challenger}")

            _open_preview(jpeg_dir / champion, jpeg_dir / challenger)

            analysis = analyze_pair(
                client,
                jpeg_dir / champion,
                jpeg_dir / challenger,
                style_rules,
            )

            while True:
                raw = input(
                    "  選択 [A=champion勝ち / B=challenger勝ち / s=スキップ / x=終了]: "
                ).strip().lower()
                if raw in ("a", "b", "s", "skip", "x", "exit"):
                    break
                print("  A / B / s / x で入力してください。")

            if raw in ("x", "exit"):
                print("\nセッションを一時終了します。")
                _save(output_path, judgments, style_rules, tournaments, by_group)
                return

            winner_str = "A" if raw == "a" else ("B" if raw == "b" else "skip")

            jdg = Judgment(
                group_id=t.group_id,
                round_num=t.round_num,
                shot_a=champion,
                shot_b=challenger,
                winner=winner_str,
                analysis=analysis[:200],
            )
            judgments.append(jdg)

            if winner_str == "B":
                t.defeated.append({
                    "file": champion,
                    "round_eliminated": t.round_num,
                    "total_rounds": len(t.shots) - 1,
                })
                t.champion = challenger
            elif winner_str == "A":
                t.defeated.append({
                    "file": challenger,
                    "round_eliminated": t.round_num,
                    "total_rounds": len(t.shots) - 1,
                })
            # skip: 現 champion を維持、次へ

            t.round_num += 1
            t.next_idx += 1
            judged_count += 1

            if t.next_idx >= len(t.shots):
                t.done = True

            # スタイルルール更新
            valid_j = [j for j in judgments if j.winner in ("A", "B")]
            if len(valid_j) > 0 and len(valid_j) % update_every == 0:
                style_rules = update_style_rules(client, valid_j, style_rules)

            _save(output_path, judgments, style_rules, tournaments, by_group)

        if t.done:
            print(f"\n  ✓ グループ {t.group_id} 完了  →  champion: {t.champion}")

    # 最終スタイルルール更新
    valid_j = [j for j in judgments if j.winner in ("A", "B")]
    if valid_j:
        style_rules = update_style_rules(client, valid_j, style_rules)

    _save(output_path, judgments, style_rules, tournaments, by_group)
    n_done = sum(1 for t in tournaments if t.done)
    print(f"\n✅ セッション完了: 判定 {judged_count} 件 / グループ {n_done}/{len(tournaments)} 完了")
    print(f"   結果: {output_path}")


def _save(
    path: Path,
    judgments: list[Judgment],
    rules: StyleRules,
    tournaments: list[GroupTournament],
    by_group: dict[int, list[ShotRecord]],
) -> None:
    ratings = compute_ratings(tournaments, by_group)
    data = {
        "judgments":   [asdict(j) for j in judgments],
        "style_rules": asdict(rules),
        "ratings":     ratings,
        "tournaments": [
            {
                "group_id":  t.group_id,
                "shots":     t.shots,
                "champion":  t.champion,
                "defeated":  t.defeated,
                "done":      t.done,
            }
            for t in tournaments
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- エントリポイント ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 3: グループ内トーナメントでベストショット選出",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("jpeg_dir")
    parser.add_argument("--csv",           default="stage2_groups.csv")
    parser.add_argument("--output",        default="stage3_results.json")
    parser.add_argument("--max-per-group", type=int, default=DEFAULT_MAX_PER_GROUP,
                        help="大グループで使うサンプル上限枚数")
    parser.add_argument("--update-every",  type=int, default=5,
                        help="N 戦ごとにスタイルルールを更新")
    parser.add_argument("--api-key",       default=None)
    parser.add_argument("--dry-run",       action="store_true",
                        help="API 不要。対戦リストとレーティング設計を表示")
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.exists():
        print(f"エラー: {jpeg_dir}", file=sys.stderr); sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = jpeg_dir.parent / csv_path
    if not csv_path.exists():
        print(f"エラー: CSV が見つかりません: {csv_path}", file=sys.stderr); sys.exit(1)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = jpeg_dir.parent / output_path

    by_group    = load_all_shots(csv_path)
    tournaments = build_tournaments(by_group, args.max_per_group, jpeg_dir)

    total_shots = sum(len(m) for m in by_group.values())
    solo_count  = sum(1 for m in by_group.values() if len(m) == 1)

    if args.dry_run:
        print(f"\n=== Aesthetic Shadowing Agent - Stage 3 [DRY-RUN] ===")
        print(f"JPEG: {jpeg_dir}")
        print(f"CSV:  {csv_path}")
        print(f"総ショット: {total_shots}  SOLO: {solo_count}  "
              f"トーナメント対象: {len(tournaments)} グループ")
        print_dry_run(tournaments, by_group)
        return

    # API キー確認
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "エラー: ANTHROPIC_API_KEY が未設定です。\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  または --api-key オプションで指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n=== Aesthetic Shadowing Agent - Stage 3 ===")
    print(f"JPEG: {jpeg_dir}")
    print(f"CSV:  {csv_path}")
    print(f"総ショット: {total_shots}  SOLO: {solo_count}  "
          f"トーナメント: {len(tournaments)} グループ  "
          f"max/グループ: {args.max_per_group}")

    client = anthropic.Anthropic(api_key=api_key)
    run_session(
        client=client,
        tournaments=tournaments,
        by_group=by_group,
        jpeg_dir=jpeg_dir,
        output_path=output_path,
        update_every=args.update_every,
    )


if __name__ == "__main__":
    main()
