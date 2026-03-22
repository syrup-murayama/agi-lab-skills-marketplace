#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Aesthetic Shadowing Agent - Stage 1〜6 セットアップ ==="

python3 -m venv .venv
echo "仮想環境を作成しました: .venv"

source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

echo ""
echo "=== ExifTool のインストール確認 ==="
if command -v exiftool &>/dev/null; then
    echo "✅ ExifTool はインストール済みです: $(exiftool --version)"
else
    echo "ExifTool が見つかりません。インストールします..."
    if command -v brew &>/dev/null; then
        brew install exiftool
        echo "✅ ExifTool をインストールしました: $(exiftool --version)"
    else
        echo "⚠️  Homebrew が見つかりません。手動でインストールしてください:"
        echo "    brew install exiftool"
    fi
fi

echo ""
echo "=== CLIP モデルのプリロード ==="
echo "CLIPモデルをプリロードしています（初回のみ約340MBのダウンロードが発生します）..."
.venv/bin/python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')" && echo "✅ CLIPプリロード完了" || echo "⚠️  CLIPプリロードに失敗しました（Stage 5 の初回実行時にダウンロードされます）"

echo ""
echo "=== MediaPipe 顔検出モデルのダウンロード ==="
STAGE2_DIR="$(dirname "$0")/../stage2"
BLAZE_FACE="$STAGE2_DIR/blaze_face.tflite"
FACE_LM="$STAGE2_DIR/face_landmarker.task"

if [ -f "$BLAZE_FACE" ]; then
    echo "✅ blaze_face.tflite はダウンロード済みです"
else
    echo "BlazeFace モデルをダウンロードしています (~224KB)..."
    curl -L -o "$BLAZE_FACE" \
      "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" \
      --progress-bar && echo "✅ blaze_face.tflite ダウンロード完了" || echo "⚠️  ダウンロード失敗"
fi

if [ -f "$FACE_LM" ]; then
    echo "✅ face_landmarker.task はダウンロード済みです"
else
    echo "FaceLandmarker モデルをダウンロードしています (~3.6MB)..."
    curl -L -o "$FACE_LM" \
      "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" \
      --progress-bar && echo "✅ face_landmarker.task ダウンロード完了" || echo "⚠️  ダウンロード失敗"
fi

echo ""
echo ""
echo "セットアップ完了。Stage 1〜6 に必要なすべての依存パッケージをインストールしました。"
echo ""
echo "  インストール済み: opencv-python, numpy, anthropic, open-clip-torch"
echo "  ※ open-clip-torch の初回実行時に約340MBのモデルが自動ダウンロードされます。"
echo ""
echo "実行例:"
echo "  source .venv/bin/activate"
echo "  python analyze.py <JPEGフォルダ>"
echo ""
