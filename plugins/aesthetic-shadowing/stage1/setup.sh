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
