# Aesthetic Shadowing Agent — 評価レポート

**測定日**: 2026-03-22 　**バージョン**: v0.1.0

---

## 1. データセット条件

| 項目 | 内容 |
|------|------|
| 撮影枚数 | **1,000〜1,500枚**（連写あり） |
| 機材 | Canon EOS R6 Mark III |
| ファイル形式 | S2 JPEG（軽量JPEG） |
| 撮影シーン | プライベート写真（家族・日常） |

---

## 2. 評価指標

| 指標 | 数値 |
|------|------|
| 人間の介入時間（処理時間削減） | **338分 → 6分** |
| ★4以上 recall（テキストモード） | **45%** |
| ★4以上 recall（画像-画像モード） | **82%** |

---

## 3. 正解の定義

- クライアント（ユーザー自身）が最終的に選んだ **★4以上のカットを正解ラベル** とする
- Stage3 でユーザーが評価した代表サンプル（〜30枚）を基準として、Stage5 が全カットに拡張適用

---

## 4. 設計の比較

| 観点 | 旧（手動） | 新（本エージェント） |
|------|-----------|---------------------|
| 作業方法 | 全枚スクロールしてセレクト | 代表 **30枚** だけ評価（約 **6分**） |
| 残りカットの処理 | 手動 | CLIP が全枚に自律適用 |
| 所要時間 | **338分** | **6分**（介入時間のみ） |

---

## 5. Smoke Test 手順

10枚の小さい JPEG フォルダで Stage1 → Stage2 → Stage6 まで通ることを確認する。

```bash
# テスト用ディレクトリを準備（10枚の JPEG を配置）
TEST_DIR=/tmp/asa_smoke_test
mkdir -p $TEST_DIR/jpeg $TEST_DIR/xmp_out

# Stage1: 白飛び・黒潰れ除外
cd plugins/aesthetic-shadowing
stage1/.venv/bin/python stage1/analyze.py $TEST_DIR/jpeg/ $TEST_DIR/xmp_out/

# Stage2: 連写グループ化 + HTML レポート生成
stage1/.venv/bin/python stage2/group.py $TEST_DIR/jpeg/ \
  --output $TEST_DIR/stage2_groups.csv

# Stage6: XMP サイドカー書き出し（Lightroom 用）
stage1/.venv/bin/python stage6/write_xmp.py \
  --scores $TEST_DIR/stage5_scores.csv \
  --output-dir $TEST_DIR/xmp_out/
```

**合格条件**: エラーなく完了し、`xmp_out/` に `.xmp` ファイルが生成されること。

---

## 6. 測定情報

| 項目 | 値 |
|------|----|
| 測定日 | **2026-03-22** |
| バージョン | **v0.1.0** |
| 実装ステータス | Stage1〜Stage3 完成、Stage4〜Stage6 実装中 |
