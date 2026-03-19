#!/bin/bash
# Aesthetic Shadowing Agent — フルパイプライン実行
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
echo "=== パイプライン完了 ==="
echo "  stage2_groups.csv → $OUT_DIR/stage2_groups.csv"
echo "  rated_samples.json → $OUT_DIR/rated_samples.json"
