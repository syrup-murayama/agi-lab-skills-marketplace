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
1. 撮影意図を伝える（Step 0-B）
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

### Step 0: SDカード検出・情報収集・コピー

> **絶対ルール: SDカードを直接参照してパイプラインを実行してはならない。`jpeg_dir` は必ずローカルパスにする。`/Volumes/...` をそのまま Step 1 以降に渡すことは禁止。**

#### 0-1. SDカードの自動検出

```bash
ls /Volumes/
find /Volumes/ -maxdepth 3 -name "DCIM" -type d 2>/dev/null
```

複数ヒットした場合はユーザーに選択を求める。

#### 0-2. カメラ機種の判定

DCIM フォルダのサブディレクトリ名でカメラ機種を判定する:

```bash
ls /Volumes/<ボリューム名>/DCIM/
```

| サブディレクトリ名 | カメラ | 保存形式 |
|---|---|---|
| `100EOS_R` など（EOS系） | EOS R6 Mark III | CFカード=RAW / SDカード=JPEG のみ |
| `100MSDCF` など（MSDCFはSony系） | Sony A7S など | RAW + JPEG 混在 |

**Sony A7S（`100MSDCF` など）を検出した場合:** ユーザーに確認する:

> 「Sony のSDカードが検出されました（`/Volumes/Untitled/DCIM/100MSDCF`）。
> RAW + JPEG が混在しています。
> - JPEGのみコピーしてセレクトを進めますか？（推奨）
> - RAW（ARW）も一緒にコピーしますか？」

- 「JPEGのみ」→ `*.JPG` のみコピー（`jpeg_dir` に格納、以降の手順と同じ）
- 「RAWも」→ JPEG を `jpeg_dir`、ARW を `raw_dir`（例: `$OUTPUT_DIR/../RAW`）にそれぞれコピー。`raw_dir` を記録しておき Step 6 で XMP サイドカーコピーに使う
- 「いいえ」→ ユーザーの指示を待つ

**重要: RAWのコピーはユーザーが明示的に希望した場合のみ行う。自動でコピーしない。**

#### 0-3. ユーザーへの一括質問（1回で全情報を収集）

以下を **1つのメッセージ** でまとめて聞く。往復を増やさない:

> 以下を教えてください:
> 1. **撮影日** — 例: `2026/03/20`、または期間 `2025-12-17〜2025-12-20`
> 2. **セッション名** — 例: `運動会2026_長男`、`202512_白山の旅`
> 3. **撮影意図** — 誰の何を撮ったか、どんな雰囲気を残したいかを自由記述
> 4. **モード** — 「自分でセレクトする」（デフォルト）or「おまかせ」

ユーザーがまとめて答えてくれた場合は、確認なしに即次の手順へ進む。

**おまかせモードの早期判定:**
ユーザーの回答に「おまかせ」「任せる」「自動で」「全部やって」などが含まれていれば、
`mode=auto` として記録する。この場合 Step 2 のダッシュボードを表示せず、Stage3〜6 を自動実行する。

#### 0-4. パス変数を確定する

ユーザーの回答を受けたら、**コピーを開始する前に** 全パスを確定する。

##### パス変数の定義（重要）

| 変数 | 役割 | 例 |
|---|---|---|
| `jpeg_dir` | JPEG 作業ディレクトリ（Stage1〜6 の処理対象） | `~/案件/2026-03-31_colorier/JPEG` |
| `raw_dir` | RAW ファイルディレクトリ（ユーザーが RAW もある場合のみ） | `~/案件/2026-03-31_colorier/RAW` |
| `OUTPUT_DIR` | 中間ファイル置き場（session.json, CSV, プロファイルなど） | `jpeg_dir` と同じパスにする |
| `stage1_xmp_dir` | Stage1 技術フィルタの **中間出力**（最終成果物ではない） | `$OUTPUT_DIR/xmp` |

> **`stage1_xmp_dir` は Lightroom に渡す最終 XMP ではない。** Stage1 が除外フラグを書いた中間ファイルで、Stage2 が参照するだけ。最終的な星レーティング XMP は Stage6 が `jpeg_dir` 内に生成し、Step 7 でコピーする。

##### SDカードからコピーする場合（ハッカソン向けフロー）

```bash
jpeg_dir=~/Downloads/2026-03-20_JPEG
OUTPUT_DIR="$jpeg_dir"
mkdir -p "$jpeg_dir"
```

##### 案件ディレクトリが既にある場合（実務フロー・推奨）

SDカードからのバックアップは手動で完了済みとする。ユーザーが案件ディレクトリのパスを指定したら、そのまま変数に設定する（`rm`, `mv`, `cp` でファイルを移動・削除してはならない）:

```bash
jpeg_dir=/path/to/案件ディレクトリ/JPEG   # ユーザー指定のパスを使う
raw_dir=/path/to/案件ディレクトリ/RAW     # RAW がある場合のみ
OUTPUT_DIR="$jpeg_dir"
stage1_xmp_dir="$OUTPUT_DIR/xmp"
mkdir -p "$stage1_xmp_dir"
```

**`jpeg_dir`, `raw_dir`, `OUTPUT_DIR`, `stage1_xmp_dir` はここで確定する。以降のすべてのステップでこの値を使う。**

#### 0-5. コピー実行（フォアグラウンド）

**重要:**
- `-newermt "yyyy-mm-dd"` は時刻なしだと前日のファイルも含む。必ず `"yyyy-mm-dd 00:00:00"` と時刻を明示する
- `-exec rsync -a {} \;` は1ファイルごとにプロセス起動するため使わない。`xargs -0 -J %` で一括コピーする

```bash
# 例: 2026-03-20 のみコピー
find /Volumes/<SDカードボリューム>/DCIM/ -name "*.JPG" \
  -newermt "2026-03-20 00:00:00" ! -newermt "2026-03-21 00:00:00" \
  -print0 | xargs -0 -J % cp % "$jpeg_dir/"

# 例: 2025-12-17〜2025-12-20 の期間コピー（終了日の翌日を上限に指定）
find /Volumes/<SDカードボリューム>/DCIM/ -name "*.JPG" \
  -newermt "2025-12-17 00:00:00" ! -newermt "2025-12-21 00:00:00" \
  -print0 | xargs -0 -J % cp % "$jpeg_dir/"
```

コピー中にユーザーへの追加質問があれば（撮影意図の補足など）この待機時間に行う。

#### 0-6. コピー完了の確認（必須）

**このステップをスキップして Step 1 に進んではならない。**

```bash
ls "$jpeg_dir" | wc -l
```

- ファイル数が 0 または著しく少ない場合 → SDカードのパスや日付指定を見直してユーザーに報告する
- ファイル数が妥当な場合 → ユーザーに「コピー完了: N枚」と報告して Step 1 へ進む

#### 0-7. session.json の生成

`session.json` は **`$OUTPUT_DIR/session.json`** に保存する（`/tmp/` や一時ディレクトリは使わない）。

Claudeが直接 Write ツールで生成する:

```json
{
  "session_name": "<セッション名>",
  "intent": "<撮影意図>",
  "jpeg_dir": "<jpeg_dir の実パス>",
  "created_at": "<ISO8601タイムスタンプ>"
}
```

またはスクリプト経由:

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage0/session_brief.py \
  --session-name "<セッション名>" \
  --output "$OUTPUT_DIR/session.json"
```

**ステップ間の確認を最小化する:**
- 各ステップ開始時は「～を実行します」と宣言するだけで、ユーザーの承認を求めない
- エラーが発生したとき以外はユーザーへの質問を挟まない

---

### Step 1: 技術フィルタリング確認

ピンボケ・白飛び・黒潰れを機械的に除外する。

**XMPディレクトリが提供されている場合** → Stage1済みとみなし、このステップをスキップ。

**XMPディレクトリがない場合** → `--help` でオプションを確認してから実行:

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage1/analyze.py --help
```

第2引数 `<xmp_dir>` は Stage1 の中間出力先（`stage1_xmp_dir`）。詳細 CSV は `<stage1_xmp_dir>/stage1_results.csv` に自動保存される。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage1/analyze.py \
  "$jpeg_dir" \
  "$stage1_xmp_dir"
```

実行後、除外枚数をユーザーに報告する。除外率が異常に高い（>50%）場合は閾値を確認する。

---

### Step 2: グループ化 + 技術スコアリング

連写グループを検出し、「最初の1枚」「最後の1枚」にボーナス重み付けを行う。
**これが30枚で全体を代表できる理由**: 各グループから代表カットを選ぶため。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage2/group.py \
  "$jpeg_dir" \
  --xmp-dir "$stage1_xmp_dir" \
  --output "$OUTPUT_DIR/stage2_groups.csv"
```

`--help` でオプションを確認すること。出力CSVのグループ数・SOLO枚数をユーザーに報告する。

> **`group.py` は manual・auto どちらのモードでも必ず実行する。**

**mode=manual（デフォルト）の場合のみ** ダッシュボードを起動する:

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage2/report.py \
  "$jpeg_dir" \
  --groups-csv "$OUTPUT_DIR/stage2_groups.csv" \
  --serve
```

ダッシュボードが起動したら、ユーザーにこう伝えて **ユーザーの返答を待つ**:

> 「ダッシュボードが開きました。写真を確認してレーティングしてください。
> セレクトが終わったら『📤 XMPに書き出す』ボタンをクリックすると Lightroom 用に書き出されます。
> 時間がない場合は『おまかせ』と言えば AI が自動で選びます。どうしますか？」

**重要: ここでユーザーの返答を受け取るまで Step 3 へ進んではならない。**
`--serve` モードはプロセスが起動し続けるため、ユーザーがダッシュボードを操作している間は待機する。

**mode=auto（おまかせ）の場合** はダッシュボードを起動せず、そのまま Step 3 へ進む。

---

### [分岐] ダッシュボード確認後の選択（manual モードのみ）

**ユーザーの返答を待ってから** 次のステップを決める:

| ユーザーの返答 | 次のステップ |
|---|---|
| ダッシュボードでセレクト完了（「できた」「終わった」「書き出した」など） | **Step 7**（Lightroom書き出し）へ直行 |
| 「おまかせ」「急いでいる」「任せる」「自動で」など | report.py を Ctrl+C で終了してから **Step 3〜6**（AI自動選別）を実行し Step 7 へ |

---

### Step 3: 審美眼サンプリング

**このステップは manual・auto どちらのモードでも実行する。**
唯一の例外: ユーザーがダッシュボードで手動セレクトを完了した場合（「できた」「書き出した」など）は Step 7 へ直行する。

> **judge.py を実行する理由**: 人間の審美眼サンプルがなければ Stage5 CLIP の学習アンカーが存在せず、スコアリングが全員同点になる。おまかせモードこそ、judge.py による代表カットのサンプリングが不可欠。

#### 既存データの確認（重要）

実行前に `$OUTPUT_DIR/rated_samples.json` が既に存在するか確認する:

```bash
ls "$OUTPUT_DIR/rated_samples.json" 2>/dev/null && echo "exists" || echo "not found"
```

- **存在する場合**: ユーザーに確認する。
  > 「前回の評価データが見つかりました（`$OUTPUT_DIR/rated_samples.json`）。このセッションの評価データとして再利用しますか？それとも最初から評価し直しますか？」
  - 「再利用」→ judge.py をスキップして Step 4 へ
  - 「やり直し」→ 以下のコマンドを実行
- **存在しない場合**: そのまま以下を実行

各グループから代表カット（技術スコア上位）を抽出し、ユーザーに1〜5のレーティングを求める。

Claudeはユーザーに事前に案内する:
> 「ブラウザが開きます。写真が1枚ずつ表示されるので、1〜5でレーティングしてください。
> 迷ったら直感で構いません。完了するとブラウザが自動で閉じます。」

以下のコマンドを**フォアグラウンドで実行**する（ブロッキング）。
ユーザーがブラウザで全枚数を評価すると judge.py が自動終了し、次のステップへ進める。

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage3/judge.py \
  "$jpeg_dir" \
  --csv "$OUTPUT_DIR/stage2_groups.csv" \
  --session "$OUTPUT_DIR/session.json" \
  --output "$OUTPUT_DIR/rated_samples.json"
```

**`--samples` オプション（省略時は `auto`）**

デフォルトの `auto` では全グループ数の10%を基準にサンプル数を自動算出し、最小20枚・最大50枚でクランプする。

| 総撮影枚数の目安 | グループ数 | 評価枚数（auto） |
|----------------|-----------|----------------|
| 〜100枚       | 〜50       | 20枚           |
| 300枚程度      | 〜150      | 20枚           |
| 600〜700枚    | 〜300      | 30枚           |
| 1,200枚以上   | 500+       | 50枚           |

枚数が多いほど Stage5 CLIP の識別精度は上がるが、人間の評価負担も増える。
撮影量・締切・用途に応じて `--samples 数値` でオーバーライド可能。

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
- `$OUTPUT_DIR/rated_samples.json`（Stage3 の出力）
- `$OUTPUT_DIR/session.json`（`intent` フィールドを取得）

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

分析結果をもとに、以下の JSON を Write ツールで `$OUTPUT_DIR/aesthetic_profile.json` に保存する:

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

**重要: `--thresholds "0.25,0.50,0.75"` を必ず指定すること。**
省略するとデフォルト4段階（★4まで）になるが、このパイプラインは ★3 までが有効範囲。

score.py には2つのモードがある:

- **`--mode text`（デフォルト）**: `aesthetic_profile.json` のテキストキーワードを視覚アンカーとして使う。セットアップが不要で即実行できる。
- **`--mode image`（推奨）**: `rated_samples.json` の評価済み画像そのものを視覚アンカーとして使う。テキストの言語バリアを回避し、撮影者の実際の選択を直接参照するため、同一撮影セッションでの識別に適している。

**テキストモード（デフォルト）**

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage5/score.py \
  --profile "$OUTPUT_DIR/aesthetic_profile.json" \
  --jpeg-dir "$jpeg_dir" \
  --output "$OUTPUT_DIR/batch_scores.csv" \
  --thresholds "0.25,0.50,0.75" \
  --verbose
```

**画像-画像モード（撮影者のサンプルがある場合に推奨）**

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage5/score.py \
  --profile "$OUTPUT_DIR/aesthetic_profile.json" \
  --rated-samples "$OUTPUT_DIR/rated_samples.json" \
  --jpeg-dir "$jpeg_dir" \
  --output "$OUTPUT_DIR/batch_scores.csv" \
  --thresholds "0.25,0.50,0.75" \
  --mode image \
  --verbose
```

`rated_samples.json` が存在する場合は `--mode image` を優先して使うこと。

---

### Step 6: メタデータ書き出し

ExifTool を使用して、メタデータに星レーティングを書き込む。

**絶対ルール: XMPファイルをClaudeが直接生成したり、インラインPythonスクリプトで書き出したりしてはならない。必ず `xmp_writer.py` を使うこと。**

**レーティング体系:**
| 値 | 意味 | 備考 |
|----|------|------|
| 0 | 未レーティング | パイプライン未評価カット |
| X（除外） | `xmp:Rating=-1` | Stage1 技術フィルタで除外 |
| 1 | KEEP | ギリギリ残す |
| 2 | FINE | 問題なし |
| 3 | GOOD | 使える |
| 4〜5 | **パイプライン禁止** | Lightroom でユーザーが現像時につける聖域 |

> **注意:** xmp_writer.py は ★4〜5 を ★3 にキャップして書き込む。4〜5 はユーザーが Lightroom で直接つけるもの。

#### JPEG への書き出し

```bash
${CLAUDE_PLUGIN_ROOT}/../../stage1/.venv/bin/python \
  ${CLAUDE_PLUGIN_ROOT}/../../stage6/xmp_writer.py \
  --scores "$OUTPUT_DIR/batch_scores.csv" \
  --image-dir "$jpeg_dir" \
  --groups-csv "$OUTPUT_DIR/stage2_groups.csv"
```

完了後、更新されたファイル数（★1/★2/★3/除外/★0 の内訳）をユーザーに報告する。

#### RAW フォルダへの XMP サイドカーコピー（ユーザーが RAW もコピーしている場合）

xmp_writer.py は JPEG 本体にレーティングを書き込むと同時に、同ディレクトリに `.xmp` ファイルも生成する。
RAW フォルダに XMP サイドカーが必要な場合は、その .xmp を RAW フォルダへコピーする:

```bash
# JPEG ディレクトリの .xmp を RAW ディレクトリへコピー
cp "$jpeg_dir"/*.xmp "$raw_dir/"
```

これにより `.ARW` と同名の `.xmp` が RAW フォルダに作成され、Lightroom で自動適用される。

> **`stage1_xmp_dir` と最終 XMP の違い（重要）**
> - `stage1_xmp_dir`（= `$OUTPUT_DIR/xmp`）: Stage1 技術フィルタリング結果の**中間出力**。Stage1〜2 でのみ使用する。Lightroom には渡さない。
> - 最終的な XMP サイドカー（Lightroom 用）は `jpeg_dir` 内に生成される。Step 7 でコピーするのは `jpeg_dir/*.xmp`。

> **exiftool で XMP サイドカーを手動生成する場合（緊急時のみ）**
> ```bash
> # 正しい書き方 — %f.xmp でファイル名をステムにマッピングする
> exiftool -ext JPG -o "$raw_dir/%f.xmp" "$jpeg_dir"
>
> # 誤った書き方（ディレクトリだけ指定すると JPEG がコピーされる）
> # exiftool -ext JPG -o "$raw_dir/" "$jpeg_dir"  ← NG
> ```
> 通常は xmp_writer.py に任せること。

---

### Step 7: Lightroom 書き出し

**CFカードへの直接書き込みは禁止。必ずローカルディレクトリへコピーする。**

#### 1. 出力先ディレクトリの確認

ユーザーに XMP サイドカーのコピー先を確認する（推奨: `~/Downloads/yyyy-mm-dd_XMP`）:

> 「XMP サイドカーの保存先を教えてください（デフォルト: `~/Downloads/yyyy-mm-dd_XMP`）」

#### 2. サイドカーをコピー

```bash
rsync -av \
  <jpeg_dir>/*.xmp \
  ~/Downloads/yyyy-mm-dd_XMP/
```

#### 3. ユーザーへの案内

コピー完了後、以下をユーザーに伝える:

> 「XMP サイドカーを `~/Downloads/yyyy-mm-dd_XMP/` にコピーしました。
> Lightroom Classic で写真を選択し、**メニュー → メタデータ → メタデータをファイルから読み込む** を実行してください。
> レーティング（1〜3星 + 除外フラグ）が反映されます。
> 4〜5星はご自身で現像後にお付けください。」

---

## 出力ファイル

| ファイル | 内容 | 次のStageへの入力 |
|----------|------|-----------------|
| `$OUTPUT_DIR/session.json` | セッション情報・撮影意図 | Stage3 |
| `$stage1_xmp_dir/stage1_results.csv` | 技術フィルタリング結果（中間出力、Lightroom には渡さない） | 参照用 |
| `$OUTPUT_DIR/stage2_groups.csv` | グループ化・技術スコア付きCSV | Stage3 |
| `$OUTPUT_DIR/rated_samples.json` | 人間レーティング付きサンプル | Stage4 |
| `$OUTPUT_DIR/aesthetic_profile.json` | Claude生成の審美眼プロファイル | Stage5 |
| `$OUTPUT_DIR/batch_scores.csv` | CLIPスコアリング結果（全カット） | Stage6 |
| `<jpeg_dir>/*.(JPG|xmp)` | 星レーティングが書き込まれた画像本体またはサイドカー | —（最終出力） |

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
  例: `$OUTPUT_DIR/stage2_groups_20260319_143022.csv`
- ユーザーのプライバシーに配慮し、写真の人物・内容を不必要に言語化しない
- スクリプトのソースは読まない。`--help` を使う（ブラックボックスとして扱う）
- `.env` などの環境変数ファイルは読まない・触らない

---

詳細リファレンス → [`reference/pipeline-overview.md`](reference/pipeline-overview.md)
トラブルシューティング → [`reference/troubleshooting.md`](reference/troubleshooting.md)
