#!/bin/bash
# doctor.sh — Aesthetic Shadowing Agent 環境チェックスクリプト

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/stage1/.venv"
SETUP_SH="$SCRIPT_DIR/stage1/setup.sh"

errors=0

echo "=== Aesthetic Shadowing Agent — 環境チェック ==="
echo ""

# 1. Python 3.10以上
PYTHON_BIN=$(command -v python3 2>/dev/null)
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Python3 が見つかりません"
    echo "   → https://www.python.org/downloads/ からインストールしてください"
    errors=$((errors + 1))
else
    PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 10 ]; }; then
        echo "✅ Python $PY_VERSION"
    else
        echo "❌ Python $PY_VERSION（3.10以上が必要）"
        echo "   → pyenv や公式サイトで Python 3.10+ をインストールしてください"
        errors=$((errors + 1))
    fi
fi

# 2. stage1/.venv が存在するか
if [ -d "$VENV" ] && [ -f "$VENV/bin/python" ]; then
    echo "✅ 仮想環境: $VENV"
else
    echo "❌ 仮想環境が見つかりません: $VENV"
    echo "   → 次のコマンドでセットアップしてください:"
    echo "     bash $SETUP_SH"
    errors=$((errors + 1))
fi

# 3. ExifTool
if command -v exiftool &>/dev/null; then
    EXIF_VER=$(exiftool --version 2>/dev/null | head -1)
    echo "✅ ExifTool $EXIF_VER"
else
    echo "❌ ExifTool が見つかりません"
    echo "   → brew install exiftool"
    errors=$((errors + 1))
fi

# 4. CLIPモデルキャッシュ
CLIP_CACHE_HF="$HOME/.cache/huggingface"
CLIP_CACHE_CLIP="$HOME/.cache/clip"
if [ -d "$CLIP_CACHE_HF" ] || [ -d "$CLIP_CACHE_CLIP" ]; then
    if [ -d "$CLIP_CACHE_HF" ]; then
        echo "✅ CLIPモデルキャッシュ: $CLIP_CACHE_HF"
    else
        echo "✅ CLIPモデルキャッシュ: $CLIP_CACHE_CLIP"
    fi
else
    echo "⚠️  CLIPモデルキャッシュが見つかりません"
    echo "   → Stage5 の初回実行時に約340MBのモデルが自動ダウンロードされます（問題なし）"
fi

# 5. Python パッケージ（cv2, torch, clip）
if [ -f "$VENV/bin/python" ]; then
    VENV_PYTHON="$VENV/bin/python"
    echo ""
    echo "--- Pythonパッケージチェック ($VENV_PYTHON) ---"

    for pkg in cv2 torch open_clip imagehash PIL flask; do
        if "$VENV_PYTHON" -c "import $pkg" 2>/dev/null; then
            VERSION=$("$VENV_PYTHON" -c "import $pkg; v = getattr($pkg, '__version__', None) or getattr($pkg, 'version', '?'); print(v)" 2>/dev/null || echo "?")
            echo "✅ import $pkg ($VERSION)"
        else
            echo "❌ import $pkg — インポート失敗"
            echo "   → bash $SETUP_SH でパッケージを再インストールしてください"
            errors=$((errors + 1))
        fi
    done
fi

# 結果サマリー
echo ""
echo "========================================"
if [ "$errors" -eq 0 ]; then
    echo "✅ すべてのチェックが通りました。パイプラインを実行できます。"
else
    echo "❌ $errors 件の問題が見つかりました。上記の修正コマンドを実行してください。"
fi
echo "========================================"
