#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Aesthetic Shadowing Agent - Stage 1 セットアップ ==="

python3 -m venv .venv
echo "仮想環境を作成しました: .venv"

source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

echo ""
echo "セットアップ完了。以下のコマンドで実行できます："
echo ""
echo "  source .venv/bin/activate"
echo "  python analyze.py <JPEGフォルダ> <CR3フォルダ>"
echo ""
