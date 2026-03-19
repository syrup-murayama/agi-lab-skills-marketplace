# Pipeline Overview — 各Stage詳細リファレンス

## アーキテクチャ図

```
[JPEG/RAW フォルダ]
       │
       ▼
   Stage 0: セッション初期化
   session_brief.py → session.json
       │
       ▼
   Stage 1: 技術フィルタリング（ローカル処理）
   analyze.py → stage1_results.csv
   除外: ピンボケ・白飛び・黒潰れ
       │
       ▼
   Stage 2: グループ化 + 技術スコアリング（ローカル処理）
   group.py → stage2_groups.csv
   連写検出・代表カット選定・ボーナス重み付け
       │
       ▼
   Stage 3: 審美眼サンプリング（LLM + 人間参加）
   judge.py → rated_samples.json
   代表30枚にユーザーがレーティング
       │
       ▼
   Stage 4: 審美眼ルール抽出（LLM）        ← 実装中
   profile.py → style_profile.json
   「このユーザーが好む写真の特徴」を言語化
       │
       ▼
   Stage 5: バッチ自動採点（LLM）           ← 実装中
   batch_score.py → final_scores.csv
   全グループにスタイルルールを適用
       │
       ▼
   Stage 6: XMPサイドカー書き出し           ← 実装中
   export_xmp.py → *.xmp
   Lightroom / Capture One 対応
```

---

## Stage 0: セッション初期化

**スクリプト**: `stage0/session_brief.py`
**処理**: セッション名・撮影意図・メタデータをJSONに保存

**入力**:
- `--session-name`: セッション識別子（例: `運動会2026_長男`）
- `--output`: 出力先JSONパス

**出力** (`session.json`):
```json
{
  "session_name": "運動会2026_長男",
  "intent": "長男の徒競走とリレーを中心に、頑張っている瞬間を記録",
  "jpeg_dir": "/Volumes/SD/DCIM/100CANON",
  "created_at": "2026-03-19T14:30:00+09:00"
}
```

---

## Stage 1: 技術フィルタリング

**スクリプト**: `stage1/analyze.py`
**処理**: 画像品質を数値化し、明らかな失敗カットを除外

**アルゴリズム**:
- ピンボケ検出: ラプラシアン分散（閾値デフォルト=80）
- 白飛び検出: 輝度255ピクセルの割合（閾値デフォルト=3%）
- 黒潰れ検出: 輝度0ピクセルの割合（閾値デフォルト=5%）

**入力**: JPEGディレクトリ
**出力** (`stage1_results.csv`):
```
filename, blur_score, overexposure_ratio, underexposure_ratio, excluded, reason
```

**注意**: XMPディレクトリが提供されている場合はスキップ可能。
Lightroomが書き出したXMPの `xmpDM:pick="-1"` を Stage1の除外フラグとして扱う。

---

## Stage 2: グループ化 + 技術スコアリング

**スクリプト**: `stage2/group.py`
**処理**: 連写グループを検出し、代表カットにスコアを付与

**グループ化ロジック**:
- EXIFの撮影時刻を使用
- `--min-visual-gap 5`: 5秒以内の連続ショットは pHash で分割しない
- `--solo-merge-gap 10`: 隣接SOLOを10秒以内でマージ

**スコアリング**:
- 基本: Stage1の技術スコア（blur, exposure）
- ボーナス: グループの最初の1枚（撮影の初期衝動）
- ボーナス: グループの最後の1枚（フォトグラファーが「これだ」と確信した1枚）

**入力**: JPEGディレクトリ + stage1_results.csv（またはXMPディレクトリ）
**出力** (`stage2_groups.csv`):
```
filename, group_id, group_type, position_in_group, tech_score, is_representative
```

**グループタイプ**:
- `BURST`: 連写グループ（2枚以上）
- `SOLO`: 単独ショット

---

## Stage 3: 審美眼サンプリング

**スクリプト**: `stage3/judge.py`
**処理**: 代表カットをユーザーに提示し、レーティングを収集

**サンプリング戦略**:
- 各グループから技術スコア上位の代表カットを選出
- 合計30枚程度になるよう調整
- ユーザーが1〜5でレーティング

**入力**:
- JPEGディレクトリ
- `--csv`: stage2_groups.csv
- `--session`: session.json
- `--output`: 出力先JSON

**出力** (`rated_samples.json`):
```json
{
  "session": { ... },
  "ratings": [
    {
      "filename": "IMG_0001.jpg",
      "group_id": "G001",
      "tech_score": 0.85,
      "user_rating": 4,
      "rated_at": "2026-03-19T14:35:22+09:00"
    }
  ]
}
```

**中断・再開**: 再実行すると未レーティング分から続きを再開する。

---

## Stage 4〜6（実装中）

### Stage 4: 審美眼ルール抽出
- `rated_samples.json` を分析し「このユーザーが好む写真の特徴」を言語化
- Claude (Sonnet) がルールを生成 → `style_profile.json`

### Stage 5: バッチ自動採点
- 全グループにスタイルルールを適用
- 信頼度スコアも付与（低信頼グループはSpotCheck対象）
- 出力: `final_scores.csv`

### Stage 6: XMPサイドカー書き出し
- Lightroom/Capture One が読み込める `.xmp` ファイルを生成
- レーティング5→★5、4→★4... として書き出し
- `xmpDM:pick` フラグも設定（-1=却下、0=未評価、1=採用）

---

## コスト試算（参考）

Claude claude-sonnet-4-6 使用時（2026年3月時点）:

| 処理 | 画像枚数 | 推定コスト |
|------|----------|-----------|
| Stage 3（30枚サンプリング） | 30枚 | ~$0.05 |
| Stage 5（330枚バッチ） | 330枚 | ~$1.20 |
| Stage 5（3000枚バッチ） | 3000枚 | ~$11.00 |

画像トークン: 2,125 tokens/枚（2400×1600、4×3タイル換算）
