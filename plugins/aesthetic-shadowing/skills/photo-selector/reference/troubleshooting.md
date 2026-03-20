# Troubleshooting — よくあるエラーと対処法

## セットアップ系

### venv が見つからない / モジュールが not found

```
ModuleNotFoundError: No module named 'cv2'
```

**原因**: Stage1の `.venv` が未作成、または依存パッケージ未インストール。

**対処**:
```bash
# セットアップスクリプトが存在する場合
bash ${CLAUDE_PLUGIN_ROOT}/../../stage1/setup.sh

# 手動でセットアップする場合
cd ${CLAUDE_PLUGIN_ROOT}/../../stage1
python3 -m venv .venv
.venv/bin/pip install opencv-python Pillow imagehash rawpy numpy anthropic
```

---

### `anthropic` モジュールが not found（Stage3）

**対処**:
```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/pip install anthropic
```

---

### ExifTool が見つからない

```
FileNotFoundError: exiftool not found
```

**対処** (macOS):
```bash
brew install exiftool
```

---

## 入力データ系

### JPEGが見つからない

**確認方法**:
```bash
ls <jpeg_dir> | head -20
```

よくある原因:
- パスに空白が含まれている → 引用符で囲む: `"path with spaces/"`
- SDカードがマウントされていない → Finderでマウント確認
- RAWファイルのみ → Stage1はJPEGを想定。RAWの場合は `--raw` オプションを確認

---

### XMP ディレクトリが空 / XMLが読めない

**確認方法**:
```bash
ls <xmp_dir>/*.xmp | head -5
```

- LightroomのXMP書き出し設定を確認
- ファイル名がJPEGと一致しているか確認（例: `IMG_0001.jpg` → `IMG_0001.xmp`）

---

## Stage別エラー

### Stage 1: 除外率が異常に高い（>50%）

**症状**: ほとんどのカットが除外される。

**原因と対処**:

| 症状 | 原因 | 対処 |
|------|------|------|
| 白飛び過多 | 屋外撮影・フラッシュ | `--overexposure-threshold` を上げる（例: 5%→10%） |
| ピンボケ過多 | 動体撮影・広角開放 | `--blur-threshold` を下げる（例: 80→50） |
| 黒潰れ過多 | 夜間・逆光 | `--underexposure-threshold` を上げる |

まず `--help` でオプション名を確認してから調整する:
```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage1/analyze.py --help
```

---

### Stage 2: グループ数が多すぎる / 少なすぎる

**グループが多すぎる（ほぼ全部SOLO）**:
```bash
# --solo-merge-gap を増やしてSOLOをマージ
--solo-merge-gap 30  # 30秒以内のSOLOをマージ
```

**グループが少なすぎる（連写でないのにBURST扱い）**:
```bash
# --min-visual-gap を減らして pHash 分割を促進
--min-visual-gap 2
```

---

### Stage 3: 写真が表示されない

**確認事項**:
1. ターミナルが画像プレビュー対応か（iTerm2推奨）
2. JPEGパスがCSVのファイル名と一致しているか
3. スクリプトの `--help` でプレビューオプションを確認

---

### Stage 3: 途中で中断してしまった

**再開方法**: 同じコマンドを再実行する。
`--output $OUTPUT_DIR/rated_samples.json` が既に存在する場合、未レーティングの写真から続きを再開する。

```bash
# 同じコマンドを再実行するだけでOK
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage3/judge.py \
  <jpeg_dir> \
  --csv $OUTPUT_DIR/stage2_groups.csv \
  --session $OUTPUT_DIR/session.json \
  --output $OUTPUT_DIR/rated_samples.json
```

---

## 出力ファイル系

### 出力先ファイルが既に存在する

**方針**: 上書きせず、タイムスタンプ付きで別名保存。

```bash
# タイムスタンプを付けて保存
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
--output $OUTPUT_DIR/stage2_groups_${TIMESTAMP}.csv
```

---

### 出力ディレクトリに書き込めない

**対処**: 書き込み権限があるディレクトリを `OUTPUT_DIR` として指定する:
```bash
--output ~/Desktop/photo-selector-output/stage2_groups.csv
```

---

## よくある質問

### Q. RAWファイル（.CR3, .ARW等）は直接使えますか？

A. Stage1〜3はJPEGを想定しています。以下の方法で対処:
1. Lightroomで「スマートプレビュー」または「JPEG書き出し」を先に実行する
2. `rawpy` を使った変換スクリプトが `stage1/` にある場合はそれを利用する

### Q. iCloud PhotosやGoogle Photosの写真は使えますか？

A. ローカルにダウンロードしてから使用してください。クラウドストレージへの直接アクセスは未対応です。

### Q. ANTHROPIC_API_KEY の設定方法は？

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

または `.zshrc` / `.bashrc` に追記して永続化する（`.env` ファイルは使わない）。
