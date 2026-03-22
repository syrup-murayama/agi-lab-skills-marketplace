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
