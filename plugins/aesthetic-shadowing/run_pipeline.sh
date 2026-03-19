#!/bin/bash
# Aesthetic Shadowing Agent — フルパイプライン実行（スタンドアロンバッチ版）
#
# 【このスクリプトの位置づけ】
#   photo-selector SKILL フローとは独立した「コマンドライン完結バッチ」。
#   Stage4 は ANTHROPIC_API_KEY が必要（profile.py を直接呼ぶため）。
#   SKILL フローでは Step 4 を Claude Code 自身が直接実行するため API キー不要。
#
# 【使い分け】
#   - 対話セッション（Claude Code）: photo-selector SKILL を使う
#   - バッチ自動実行 / CI: このスクリプトを使う（ANTHROPIC_API_KEY 環境変数が必要）
#
# 使い方: ./run_pipeline.sh <jpeg_dir> <xmp_dir> <output_dir> [session_json]
#
# 例:
#   ./run_pipeline.sh \
#     /Users/daisuke/Pictures/ASA-test-data/v1.0.1/S2_JPEG \
#     /Users/daisuke/Pictures/ASA-test-data/v1.0.1/xmp_output \
#     /Users/daisuke/Pictures/ASA-test-data/v1.0.1 \
#     /Users/daisuke/Pictures/ASA-test-data/v1.0.1/session.json

VENV="$(dirname "$0")/stage1/.venv/bin/python"
JPEG_DIR="$1"
XMP_DIR="$2"
OUT_DIR="$3"
SESSION_JSON="${4:-}"

if [ -z "$JPEG_DIR" ] || [ -z "$XMP_DIR" ] || [ -z "$OUT_DIR" ]; then
  echo "使い方: $0 <jpeg_dir> <xmp_dir> <output_dir> [session_json]"
  exit 1
fi

set -e

echo "=== Aesthetic Shadowing Pipeline ==="
echo "JPEG: $JPEG_DIR"
echo "XMP:  $XMP_DIR"
echo "OUT:  $OUT_DIR"
echo ""

# Stage 2: グループ化（Stage1除外を反映）
echo "[Stage 2] グループ化 + 技術スコアリング..."
"$VENV" "$(dirname "$0")/stage2/group.py" \
  "$JPEG_DIR" \
  --xmp-dir "$XMP_DIR" \
  --output "$OUT_DIR/stage2_groups.csv" \
  --verbose

# Stage 3: 審美眼サンプリング
echo ""
echo "[Stage 3] 30枚レーティング..."
STAGE3_ARGS=("$JPEG_DIR" "--csv" "$OUT_DIR/stage2_groups.csv" "--output" "$OUT_DIR/rated_samples.json")
if [ -n "$SESSION_JSON" ]; then
  STAGE3_ARGS+=("--session" "$SESSION_JSON")
fi
"$VENV" "$(dirname "$0")/stage3/judge.py" "${STAGE3_ARGS[@]}"

echo ""

# Stage 4: 審美眼プロファイル生成（rated_samples.json が存在する場合のみ）
if [ -f "$OUT_DIR/rated_samples.json" ]; then
  echo "[Stage 4] 審美眼プロファイル生成..."
  STAGE4_ARGS=("--rated" "$OUT_DIR/rated_samples.json" "--jpeg-dir" "$JPEG_DIR" "--mode" "text" "--output" "$OUT_DIR/aesthetic_profile.json")
  if [ -n "$SESSION_JSON" ]; then
    STAGE4_ARGS+=("--session" "$SESSION_JSON")
  fi
  "$VENV" "$(dirname "$0")/stage4/profile.py" "${STAGE4_ARGS[@]}"

  echo ""

  # Stage 5: CLIPバッチスコアリング
  echo "[Stage 5] CLIPバッチスコアリング..."
  "$VENV" "$(dirname "$0")/stage5/score.py" \
    --profile "$OUT_DIR/aesthetic_profile.json" \
    --jpeg-dir "$JPEG_DIR" \
    --output "$OUT_DIR/batch_scores.csv" \
    --verbose

  echo ""

  # Stage 6: XMP星レーティング書き出し
  echo "[Stage 6] XMP星レーティング書き出し..."
  "$VENV" "$(dirname "$0")/stage6/xmp_writer.py" \
    --scores "$OUT_DIR/batch_scores.csv" \
    --xmp-dir "$OUT_DIR/xmp_rated" \
    --overwrite
else
  echo "[Stage 4-6] rated_samples.json が見つからないためスキップ。"
  echo "  先に Stage 3 を完了してください。"
fi

echo ""
echo "=== パイプライン完了 ==="
echo "  stage2_groups.csv   → $OUT_DIR/stage2_groups.csv"
echo "  rated_samples.json  → $OUT_DIR/rated_samples.json"
if [ -f "$OUT_DIR/aesthetic_profile.json" ]; then
  echo "  aesthetic_profile.json → $OUT_DIR/aesthetic_profile.json"
  echo "  batch_scores.csv    → $OUT_DIR/batch_scores.csv"
  echo "  xmp_rated/          → $OUT_DIR/xmp_rated/"
fi
