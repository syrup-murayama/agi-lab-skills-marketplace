---
name: photo-selector
description: >
  フォトグラファーが「この写真をセレクトして」「RAWデータを整理したい」
  「撮影済みのSDカードから良いカットを選んでほしい」「写真の仕分けを手伝って」
  「Lightroomに取り込む前に絞り込みたい」と言ったとき、必ずこのスキルを使う。
  JPEGフォルダ、RAWフォルダ、SDカードのパスが示されたときも使う。
  撮影意図（運動会・学校PR・旅行・結婚式など）を組み合わせた選定にも対応。
user-invocable: true
---

# Photo Selector — 写真セレクト自律エージェント

## このSKILLの目的（WHY）

フォトグラファーの「審美眼」をAIに継承させるセレクト支援エージェント。

写真セレクトには2種類の判断が必要:
1. **技術的判断** — ピンボケ・白飛び・黒潰れは機械が確実に検出できる
2. **審美的判断** — 「この表情が好き」「このトーンが合う」は人間にしかわからない

このSKILLは両方を組み合わせる。ポイントは**30枚の人間判断で2500枚を自動採点できる**こと。
Stage3で学習した「あなたの審美眼ルール」を、残り全カットにClaudeが自律適用する。

**人間の介入は2回だけ:**
1. 撮影意図を伝える（Step 0）
2. 代表30枚にレーティングする（Step 3）

あとはClaudeが全て自律的に進める。

---

## 初回セットアップ

### 依存環境のインストール
```bash
bash ${CLAUDE_PLUGIN_ROOT}/../../stage1/setup.sh
```
これだけで Stage1〜6 に必要なすべての依存パッケージをインストールする。

### ローカルLLM（CLIP）について
Stage5 では OpenAI CLIP をローカルで実行する（APIコスト ゼロ）。
初回実行時に約340MBのモデルが自動ダウンロードされる。
以降はキャッシュされる（`~/.cache/clip/`）。

Apple Silicon Mac の場合は MPS が自動で有効になり高速動作する。

---

## 実行フロー

### Step 0: 撮影意図の確認

Claudeがユーザーに自然な会話で以下を確認する（CLIツール不要）:

- **セッション名**: 例 `運動会2026_長男`、`school-PR-march`
- **撮影意図**: 自由記述。「誰の何を撮った写真か」「どんな雰囲気を残したいか」
- **JPEGフォルダのパス**: セレクト対象の画像が入っているディレクトリ
- **XMP出力先**（任意）: Stage1済みXMLがある場合のディレクトリ

確認できたら `session.json` を生成する。スクリプトが使える場合:

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage0/session_brief.py \
  --session-name "<セッション名>" \
  --output /tmp/session.json
```

スクリプトが不要な場合はClaudeが直接Writeツールで生成してもよい:

```json
{
  "session_name": "<セッション名>",
  "intent": "<撮影意図>",
  "jpeg_dir": "<JPEGフォルダのパス>",
  "created_at": "<ISO8601タイムスタンプ>"
}
```

---

### Step 1: 技術フィルタリング確認

ピンボケ・白飛び・黒潰れを機械的に除外する。

**XMPディレクトリが提供されている場合** → Stage1済みとみなし、このステップをスキップ。

**XMPディレクトリがない場合** → `--help` でオプションを確認してから実行:

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage1/analyze.py --help
```

第2引数 `<xmp_dir>` は XMP サイドカーの書き出し先。CR3 フォルダがなければ任意のディレクトリを指定してよい。
詳細 CSV は `<xmp_dir>/stage1_results.csv` に自動保存される。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage1/analyze.py \
  <jpeg_dir> \
  <xmp_dir>
```

実行後、除外枚数をユーザーに報告する。除外率が異常に高い（>50%）場合は閾値を確認する。

---

### Step 2: グループ化 + 技術スコアリング

連写グループを検出し、「最初の1枚」「最後の1枚」にボーナス重み付けを行う。
**これが30枚で全体を代表できる理由**: 各グループから代表カットを選ぶため。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage2/group.py \
  <jpeg_dir> \
  --xmp-dir <xmp_dir> \
  --output /tmp/stage2_groups.csv
```

`--help` でオプションを確認すること。出力CSVのグループ数・SOLO枚数をユーザーに報告する。

---

### Step 3: 審美眼サンプリング（人間参加ステップ）

**ここだけユーザーが参加する。所要時間: 約6分。**

各グループから代表カット（技術スコア上位）を抽出し、ユーザーに1〜5のレーティングを求める。

Claudeはユーザーに事前に案内する:
> 「ブラウザが開きます。写真が1枚ずつ表示されるので、1〜5でレーティングしてください。
> 迷ったら直感で構いません。6分ほどで完了します。完了するとブラウザが自動で閉じます。」

以下のコマンドを**フォアグラウンドで実行**する（ブロッキング）。
ユーザーがブラウザで全枚数を評価すると judge.py が自動終了し、次のステップへ進める。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage3/judge.py \
  <jpeg_dir> \
  --csv /tmp/stage2_groups.csv \
  --session /tmp/session.json \
  --output /tmp/rated_samples.json
```

**重要: バックグラウンド実行（`&` や `run_in_background`）は使わない。**
フォアグラウンドで待機することで、完了を自動検知できる。

完了後、`rated_samples.json` の内容を要約してユーザーに報告する:
- 何枚レーティングしたか
- 高評価（4〜5）・低評価（1〜2）の傾向
- 次のステップ（Stage4以降）の予告

---

### Step 4: 審美眼プロファイル生成（Claude ネイティブ）

`rated_samples.json` が存在する場合のみ実行する。

**このステップは Claude 自身が直接実行する（API キー不要）。**
外部スクリプトを呼び出さず、以下の手順でプロファイルを生成する。

**1. データ読み込み**

Read ツールで以下を読み込む:
- `/tmp/rated_samples.json`（Stage3 の出力）
- `/tmp/session.json`（`intent` フィールドを取得）

**2. サンプル分類**

`skipped: true` のものを除外し:
- 高評価: `human_rating >= 4`
- 低評価: `human_rating <= 2`
- 統計: 合計・高評価数・低評価数・平均レーティング・分布

**3. 視覚分析（推奨）**

高評価サンプルを `learning_weight` 降順でソートし、上位最大 10 枚を
Read ツールで画像として読み込む（パス: `<jpeg_dir>/<file>`）。
画像が読めない場合はテキスト分析にフォールバックする。

**4. プロファイル生成**

分析結果をもとに、以下の JSON を Write ツールで `/tmp/aesthetic_profile.json` に保存する:

```json
{
  "session_name": "<セッション名>",
  "created_at": "<ISO8601タイムスタンプ>",
  "mode": "vision",
  "intent": "<撮影意図>",
  "profile_text": "高評価・低評価それぞれの傾向を3〜5文で説明する文章",
  "clip_query": "Stage5 CLIP 検索クエリ（日本語可、20字以内）",
  "high_keywords": ["高評価に共通するキーワードを3〜6個"],
  "low_keywords": ["低評価に共通するキーワードを3〜6個"],
  "stats": {
    "total_rated": 0,
    "high_count": 0,
    "low_count": 0,
    "avg_rating": 0.0,
    "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
  },
  "model": "claude-native"
}
```

完了後、プロファイルの要点（profile_text・clip_query・high/low keywords）をユーザーに報告する。

---

### Step 5: CLIPバッチスコアリング

ローカルCLIPモデルで全カットをスコアリングする（APIコストゼロ）。

score.py には2つのモードがある:

- **`--mode text`（デフォルト）**: `aesthetic_profile.json` のテキストキーワードを視覚アンカーとして使う。セットアップが不要で即実行できる。
- **`--mode image`（推奨）**: `rated_samples.json` の評価済み画像そのものを視覚アンカーとして使う。実際の評価サンプルがある場合はこちらのほうが精度が高い（★4 recall: テキスト45% → 画像82%）。

**テキストモード（デフォルト）**

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage5/score.py \
  --profile /tmp/aesthetic_profile.json \
  --jpeg-dir <jpeg_dir> \
  --output /tmp/batch_scores.csv \
  --verbose
```

**画像-画像モード（撮影者のサンプルがある場合に推奨）**

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage5/score.py \
  --profile /tmp/aesthetic_profile.json \
  --rated-samples /tmp/rated_samples.json \
  --jpeg-dir <jpeg_dir> \
  --output /tmp/batch_scores.csv \
  --mode image \
  --verbose
```

`rated_samples.json` が存在する場合は `--mode image` を優先して使うこと。

---

### Step 6: XMP星レーティング書き出し

Lightroom対応のXMPサイドカーを生成する。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage6/xmp_writer.py \
  --scores /tmp/batch_scores.csv \
  --xmp-dir /tmp/xmp_rated \
  --overwrite
```

完了後、生成されたXMPファイル数と出力先をユーザーに報告する。

---

## 出力ファイル

| ファイル | 内容 | 次のStageへの入力 |
|----------|------|-----------------|
| `/tmp/session.json` | セッション情報・撮影意図 | Stage3 |
| `<xmp_dir>/stage1_results.csv` | 技術フィルタリング結果（xmp_dir に自動保存） | 参照用 |
| `/tmp/stage2_groups.csv` | グループ化・技術スコア付きCSV | Stage3 |
| `/tmp/rated_samples.json` | 人間レーティング付きサンプル | Stage4 |
| `/tmp/aesthetic_profile.json` | Claude生成の審美眼プロファイル | Stage5 |
| `/tmp/batch_scores.csv` | CLIPスコアリング結果（全カット） | Stage6 |
| `/tmp/xmp_rated/*.xmp` | Lightroom対応レーティング | —（最終出力） |

---

## エラーハンドリング

**JPEGが見つからない**
→ パスのタイポを確認。`ls <path>` で存在確認してからユーザーに伝える。

**venv / 依存パッケージが不足**
→ `${CLAUDE_PLUGIN_ROOT}/../../stage1/` に `.venv` があるか確認。
→ なければ `setup.sh` を案内: `bash ${CLAUDE_PLUGIN_ROOT}/../../stage1/setup.sh`

**Stage3 を中断した場合**
→ 同じコマンドを再実行すると続きから再開できることをユーザーに伝える。

**除外率が異常（>50%）**
→ Stage1の閾値が厳しすぎる可能性。`--help` で閾値オプションを確認し、
  `--blur-threshold` や `--overexposure-threshold` の調整をユーザーに提案する。

---

## 注意事項

- `rm` コマンドは使わない（CLAUDE.mdルール）
- 出力先が既存ファイルと重複する場合はタイムスタンプを付けて別名保存
  例: `/tmp/stage2_groups_20260319_143022.csv`
- ユーザーのプライバシーに配慮し、写真の人物・内容を不必要に言語化しない
- スクリプトのソースは読まない。`--help` を使う（ブラックボックスとして扱う）
- `.env` などの環境変数ファイルは読まない・触らない

---

詳細リファレンス → [`reference/pipeline-overview.md`](reference/pipeline-overview.md)
トラブルシューティング → [`reference/troubleshooting.md`](reference/troubleshooting.md)
