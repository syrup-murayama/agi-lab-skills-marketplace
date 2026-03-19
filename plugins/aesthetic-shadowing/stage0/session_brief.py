"""
Stage 0: Session Brief
撮影意図をユーザーから受け取り、構造化JSONとして保存するCLIツール。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# カテゴリ判定キーワード（優先度順に評価）
CATEGORY_KEYWORDS = {
    "portrait":     ["ポートレート", "撮影会", "モデル"],
    "social_event": ["結婚式", "パーティー", "友人", "感動", "編集", "ビデオ"],
    "client_pr":    ["クライアント", "入学", "学校", "企業", "設備", "PR", "広報"],
    "travel":       ["旅行", "観光"],
    "family_event": ["子ども", "家族", "運動会", "発表会", "記録", "アルバム"],
}

# output_use 判定キーワード
OUTPUT_USE_KEYWORDS = {
    "client_delivery": ["クライアント", "納品", "納入", "依頼"],
    "social_media":    ["SNS", "インスタ", "Twitter", "投稿"],
    "print":           ["印刷", "プリント", "プリンタ"],
    "video":           ["動画", "ビデオ", "映像", "編集"],
    "personal_album":  ["アルバム", "記録", "思い出"],
}


def detect_keywords(text: str) -> list[str]:
    """テキストから意図キーワードを抽出する。"""
    candidates = [
        "笑顔", "表情", "生き生き", "瞬間",
        "子ども", "長男", "次男", "長女", "彼", "彼女",
        "友達", "関わり", "みんな", "グループ",
        "記録", "アルバム", "思い出",
        "クライアント", "PR", "広報",
        "旅行", "観光",
        "ポートレート", "モデル",
        "結婚式", "パーティー",
    ]
    found = [kw for kw in candidates if kw in text]
    # 重複除去しながら順序保持
    seen: set[str] = set()
    return [kw for kw in found if not (kw in seen or seen.add(kw))]  # type: ignore[func-returns-value]


def classify_category(text: str) -> str:
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return "other"


def classify_output_use(text: str, category: str) -> str:
    for use, keywords in OUTPUT_USE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return use
    # カテゴリから推定
    if category == "client_pr":
        return "client_delivery"
    if category == "social_event":
        return "social_media"
    if category == "travel":
        return "personal_album"
    return "personal_album"


def build_selection_hints(text: str, category: str) -> dict:
    expressions_kws = ["笑顔", "表情", "生き生き", "瞬間"]
    subject_kws = ["子ども", "長男", "次男", "長女", "彼", "彼女", "家族"]
    group_kws = ["友達", "関わり", "みんな", "グループ"]

    return {
        "prioritize_expressions":      any(kw in text for kw in expressions_kws),
        "prioritize_specific_subject": any(kw in text for kw in subject_kws),
        "include_group_shots":         any(kw in text for kw in group_kws),
        "output_use":                  classify_output_use(text, category),
    }


def read_multiline_intent() -> str:
    """空行2回で確定するマルチライン入力を受け付ける。"""
    print("撮影意図を入力してください。")
    print("このセレクトで何を実現したいか、自由に記述してください:")
    print("（空行を2回入力すると確定します）")

    lines: list[str] = []
    consecutive_blank = 0
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line == "":
            consecutive_blank += 1
            if consecutive_blank >= 2:
                break
            lines.append(line)
        else:
            consecutive_blank = 0
            lines.append(line)

    # 末尾の空行を除去
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 0: セッション初期化ツール",
    )
    parser.add_argument("--output", default="session.json", help="出力JSONファイルパス")
    parser.add_argument("--session-name", help="セッション名（省略時はインタラクティブ入力）")
    args = parser.parse_args()

    print("=== Aesthetic Shadowing Agent - Stage 0: セッション初期化 ===")
    print()

    # セッション名
    if args.session_name:
        session_name = args.session_name
    else:
        print("セッション名を入力してください（例: 運動会2026_長男）:")
        session_name = input("> ").strip()
        if not session_name:
            print("エラー: セッション名は必須です。", file=sys.stderr)
            sys.exit(1)
    print()

    # 撮影意図
    intent_raw = read_multiline_intent()
    if not intent_raw.strip():
        print("エラー: 撮影意図は必須です。", file=sys.stderr)
        sys.exit(1)
    print()

    # 確認
    print("確認:")
    print(f"  セッション名: {session_name}")
    preview = intent_raw[:80].replace("\n", " ")
    if len(intent_raw) > 80:
        preview += "..."
    print(f"  意図: {preview}")
    print()
    try:
        answer = input("この内容で保存しますか？ [Y/n]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("n", "no"):
        print("キャンセルしました。")
        sys.exit(0)

    # 解析
    category = classify_category(intent_raw)
    keywords = detect_keywords(intent_raw)
    hints = build_selection_hints(intent_raw, category)

    session_data = {
        "session_name":   session_name,
        "created_at":     datetime.now().isoformat(timespec="seconds"),
        "intent_raw":     intent_raw,
        "intent_keywords": keywords,
        "intent_category": category,
        "selection_hints": hints,
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"保存しました: {output_path.resolve()}")
    print(f"  カテゴリ: {category}")
    print(f"  キーワード: {', '.join(keywords) if keywords else '（なし）'}")
    print(f"  output_use: {hints['output_use']}")


if __name__ == "__main__":
    main()
