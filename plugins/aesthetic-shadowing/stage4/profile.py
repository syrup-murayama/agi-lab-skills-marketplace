#!/usr/bin/env python3
"""
Stage 4A/4B: 審美眼プロファイル生成

rated_samples.json（Stage3の出力）から「審美眼プロファイル」を生成し、
aesthetic_profile.json として保存する。

モード:
  --mode text   : テキストのみ。スコア・メタデータからプロファイルを生成（Claude API 1回）
  --mode vision : 高評価画像を Claude API に渡して視覚的プロファイルを生成（最大10枚）

使い方:
  python profile.py \
    --rated <rated_samples.json> \
    --session <session.json> \
    --jpeg-dir <jpeg_dir> \
    --mode text|vision \
    --output <aesthetic_profile.json>
"""

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

MODEL = "claude-sonnet-4-6"
MAX_VISION_IMAGES = 10
HIGH_RATING_THRESHOLD = 4
LOW_RATING_THRESHOLD = 2


# ---- データ読み込み ----

def load_rated(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"エラー: rated_samples.json の読み込みに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)


def load_session_intent(session_path: Path | None) -> str:
    if session_path is None or not session_path.exists():
        return ""
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        return data.get("intent", "")
    except Exception:
        return ""


# ---- サンプル分類 ----

def classify_samples(samples: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """高評価・低評価・全評価済みに分類する。skippedは除外。"""
    rated = [s for s in samples if s.get("human_rating") is not None and not s.get("skipped", False)]
    high = [s for s in rated if s["human_rating"] >= HIGH_RATING_THRESHOLD]
    low  = [s for s in rated if s["human_rating"] <= LOW_RATING_THRESHOLD]
    return rated, high, low


def compute_stats(rated: list[dict], high: list[dict], low: list[dict]) -> dict:
    total = len(rated)
    avg = sum(s["human_rating"] for s in rated) / total if total > 0 else 0.0
    dist = {str(i): sum(1 for s in rated if s["human_rating"] == i) for i in range(1, 6)}
    return {
        "total_rated": total,
        "high_count":  len(high),
        "low_count":   len(low),
        "avg_rating":  round(avg, 2),
        "distribution": dist,
    }


# ---- プロンプト構築 ----

def _sample_lines(samples: list[dict]) -> str:
    lines = []
    for s in samples:
        lines.append(
            f"  - {s['file']}  group={s['group_id']}  "
            f"rating={s['human_rating']}  tech={s['technical_score']:.3f}  "
            f"weight={s.get('learning_weight', 0):.2f}"
        )
    return "\n".join(lines) if lines else "  （なし）"


def build_text_prompt(
    session_name: str,
    intent: str,
    high: list[dict],
    low: list[dict],
    stats: dict,
) -> str:
    return f"""あなたはプロフォトグラファーの審美眼を分析するアシスタントです。

## セッション情報
- セッション名: {session_name}
- 撮影意図: {intent or "（未設定）"}

## 評価統計
- 評価済み合計: {stats['total_rated']} 枚
- 高評価（★4-5）: {stats['high_count']} 枚
- 低評価（★1-2）: {stats['low_count']} 枚
- 平均レーティング: {stats['avg_rating']}

## 高評価サンプル（★4-5）
{_sample_lines(high)}

## 低評価サンプル（★1-2）
{_sample_lines(low)}

---

上記の情報をもとに、以下の JSON を **日本語** で生成してください。
マークダウンコードブロックなしで、JSONのみ出力してください。

{{
  "profile_text": "高評価写真の傾向と低評価写真の傾向を3〜5文で説明する文章",
  "clip_query": "Stage5のCLIP検索で使う短いクエリ文字列（日本語可、20字以内）",
  "high_keywords": ["高評価に共通するキーワードを3〜6個"],
  "low_keywords": ["低評価に共通するキーワードを3〜6個"]
}}"""


def build_vision_prompt(
    session_name: str,
    intent: str,
    high: list[dict],
    low: list[dict],
    stats: dict,
) -> str:
    return f"""あなたはプロフォトグラファーの審美眼を分析するアシスタントです。

添付した画像はすべて **高評価（★4〜5）** と評価された写真です。

## セッション情報
- セッション名: {session_name}
- 撮影意図: {intent or "（未設定）"}
- 評価済み合計: {stats['total_rated']} 枚 / 高評価: {stats['high_count']} 枚 / 低評価: {stats['low_count']} 枚
- 平均レーティング: {stats['avg_rating']}

## 低評価サンプル参考情報（★1-2、画像なし）
{_sample_lines(low[:5])}

---

画像を視覚的に分析し、以下の JSON を **日本語** で生成してください。
マークダウンコードブロックなしで、JSONのみ出力してください。

{{
  "profile_text": "高評価写真の視覚的な傾向と共通点を3〜5文で詳しく説明する文章",
  "clip_query": "Stage5のCLIP検索で使う短いクエリ文字列（日本語可、20字以内）",
  "high_keywords": ["視覚的特徴に基づくキーワードを3〜6個"],
  "low_keywords": ["低評価に共通すると思われるキーワードを3〜6個"]
}}"""


# ---- Claude API 呼び出し ----

def call_text_mode(
    client,
    session_name: str,
    intent: str,
    high: list[dict],
    low: list[dict],
    stats: dict,
) -> dict:
    prompt = build_text_prompt(session_name, intent, high, low, stats)
    print("Claude API を呼び出し中（テキストモード）...", flush=True)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_claude_json(response.content[0].text)


def call_vision_mode(
    client,
    session_name: str,
    intent: str,
    high: list[dict],
    low: list[dict],
    stats: dict,
    jpeg_dir: Path,
) -> dict:
    # 高評価画像を最大 MAX_VISION_IMAGES 枚選ぶ（learning_weight 降順）
    high_sorted = sorted(high, key=lambda s: s.get("learning_weight", 0), reverse=True)
    selected = []
    for s in high_sorted:
        img_path = jpeg_dir / s["file"]
        if img_path.exists():
            selected.append((s, img_path))
        if len(selected) >= MAX_VISION_IMAGES:
            break

    if not selected:
        print("警告: 高評価画像が見つかりません。テキストモードにフォールバックします。", file=sys.stderr)
        return call_text_mode(client, session_name, intent, high, low, stats)

    print(f"Claude API を呼び出し中（ビジョンモード、{len(selected)}枚）...", flush=True)

    # マルチパートコンテンツを組み立てる
    content = []
    for s, img_path in selected:
        img_bytes = img_path.read_bytes()
        b64 = base64.standard_b64encode(img_bytes).decode("ascii")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        })
        content.append({
            "type": "text",
            "text": f"↑ {s['file']}  rating={s['human_rating']}  tech={s['technical_score']:.3f}",
        })

    prompt_text = build_vision_prompt(session_name, intent, high, low, stats)
    content.append({"type": "text", "text": prompt_text})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    return parse_claude_json(response.content[0].text)


def parse_claude_json(text: str) -> dict:
    """Claude の応答テキストから JSON を抽出する。"""
    text = text.strip()
    # コードブロックが含まれていた場合の保険
    if text.startswith("```"):
        lines = text.splitlines()
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"警告: Claude の応答を JSON としてパースできませんでした: {e}", file=sys.stderr)
        print("--- 生テキスト ---", file=sys.stderr)
        print(text, file=sys.stderr)
        # フォールバック: profile_text に生テキストを入れる
        return {
            "profile_text": text,
            "clip_query": "",
            "high_keywords": [],
            "low_keywords": [],
        }


# ---- メイン ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 4: rated_samples.json から審美眼プロファイルを生成する",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rated",    required=True,
                        help="Stage3 の出力（rated_samples.json）")
    parser.add_argument("--session",  default=None,
                        help="session.json（intent フィールドを含む）。省略可。")
    parser.add_argument("--jpeg-dir", default=None,
                        help="JPEG 画像ディレクトリ（--mode vision 時に必須）")
    parser.add_argument("--mode",     choices=["text", "vision"], default="text",
                        help="text: テキストのみ / vision: 高評価画像を Claude に渡す")
    parser.add_argument("--output",   default="aesthetic_profile.json",
                        help="出力 JSON ファイル名")
    args = parser.parse_args()

    rated_path = Path(args.rated)
    if not rated_path.exists():
        print(f"エラー: rated_samples.json が見つかりません: {rated_path}", file=sys.stderr)
        sys.exit(1)

    session_path = Path(args.session) if args.session else None
    output_path  = Path(args.output)

    jpeg_dir: Path | None = None
    if args.mode == "vision":
        if args.jpeg_dir is None:
            print("エラー: --mode vision には --jpeg-dir が必要です。", file=sys.stderr)
            sys.exit(1)
        jpeg_dir = Path(args.jpeg_dir)
        if not jpeg_dir.exists():
            print(f"エラー: JPEG ディレクトリが見つかりません: {jpeg_dir}", file=sys.stderr)
            sys.exit(1)

    # データ読み込み
    rated_data   = load_rated(rated_path)
    session_name = rated_data.get("session_name", "")
    intent       = load_session_intent(session_path)

    samples             = rated_data.get("samples", [])
    rated, high, low    = classify_samples(samples)
    stats               = compute_stats(rated, high, low)

    print(f"セッション: {session_name or '（名前なし）'}")
    print(f"評価済み: {stats['total_rated']} 枚  高評価: {stats['high_count']}  低評価: {stats['low_count']}  平均: {stats['avg_rating']}")

    if stats["total_rated"] == 0:
        print("エラー: 評価済みサンプルが 0 枚です。judge.py でレーティングを完了してください。", file=sys.stderr)
        sys.exit(1)

    # .env ファイルがあれば読み込む（python-dotenv 任意）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv 未インストールでも動作する

    # anthropic インポート（venv に入っていない場合はエラーメッセージを出す）
    try:
        import anthropic
    except ImportError:
        print(
            "エラー: anthropic パッケージが見つかりません。\n"
            "  cd plugins/aesthetic-shadowing/stage1\n"
            "  .venv/bin/pip install anthropic",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 環境変数を自動参照

    # Claude API 呼び出し
    if args.mode == "vision":
        claude_result = call_vision_mode(client, session_name, intent, high, low, stats, jpeg_dir)
    else:
        claude_result = call_text_mode(client, session_name, intent, high, low, stats)

    # プロファイル組み立て
    profile = {
        "session_name": session_name,
        "created_at":   datetime.now().isoformat(),
        "mode":         args.mode,
        "intent":       intent,
        "profile_text": claude_result.get("profile_text", ""),
        "clip_query":   claude_result.get("clip_query", ""),
        "high_keywords": claude_result.get("high_keywords", []),
        "low_keywords":  claude_result.get("low_keywords", []),
        "stats":        stats,
        "model":        MODEL,
    }

    output_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nプロファイル保存完了: {output_path}")
    print(f"  CLIP クエリ: {profile['clip_query']}")
    print(f"  高評価キーワード: {', '.join(profile['high_keywords'])}")
    print(f"  低評価キーワード: {', '.join(profile['low_keywords'])}")


if __name__ == "__main__":
    main()
