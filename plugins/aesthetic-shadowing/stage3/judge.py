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

    print(f"\n=== Aesthetic Shadowing Agent - Stage 3 ===")
    print(f"JPEG: {jpeg_dir}")
    print(f"CSV:  {csv_path}")

    candidates = load_groups(csv_path, jpeg_dir)
    print(f"比較可能グループ数: {len(candidates)}")

    if not candidates:
        print("比較できるグループがありません（全グループが SOLO）。")
        sys.exit(0)

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 環境変数から読む

    run_session(
        client=client,
        candidates=candidates,
        rounds=args.rounds,
        output_path=output_path,
        update_every=args.update_every,
    )


if __name__ == "__main__":
    main()
