# 📷 Aesthetic Shadowing

**写真家の審美眼を継承して最後まで走る自律エージェント**

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Claude AI](https://img.shields.io/badge/Claude-AI-orange)
![CLIP](https://img.shields.io/badge/CLIP-ローカル実行-green)
![ExifTool](https://img.shields.io/badge/ExifTool-必須-lightgrey)

---

## なぜこれが必要か

家族写真を撮るとき、こんな体験をしたことはないだろうか。

運動会、誕生日、子どもの発表会。シャッターを切るたびに「これは良い」と感じる瞬間がある。帰宅してパソコンを開く。あの瞬間の写真を確認しようとズームして、気づく——**ピンボケ。**

あるいは目を閉じている。あるいは白飛びしている。

「セレクト自体は楽しいはずなのに、失敗カットを見極める作業がつらい」

何百枚もある写真を1枚ずつ開いて、ズームして、確認して、次へ。そのループが、撮影の喜びを少しずつ削っていく。

**最高の一枚を選ぶより、失敗カットに気づかず感動する瞬間を壊さないことの方が大切だった。**

---

## The Solution — 6分だけ審美眼を預けると、あとは全部やっておく

Aesthetic Shadowing はエージェントだ。ダッシュボードではない。

6分間、30枚にレーティングをつける。それだけでいい。
エージェントはその判断から審美眼ルールを言語化し、残り数千枚を自律的にスコアリングし、Lightroomでそのまま読み込める形（XMPメタデータ）で返す。

途中で止めても、次回 `rated_samples.json` から再開する。
依存パッケージが足りなければ、`setup.sh` の実行を提案して環境を整えてから続行する。

**人間の関与時間：枚数によらず常に6分。**

---

## Pipeline Overview

```
Stage 0: ヒアリング（撮影意図の確認）
Stage 1: 技術フィルタリング（完全白飛び・黒潰れの絶対除外）
Stage 2: グループ化 + ダッシュボード
Stage 3: 審美眼サンプリング（代表カットを人間がレーティング ← ここだけ6分）
Stage 4: 審美眼プロファイル生成（Claude が言語化）
Stage 5: CLIP バッチスコアリング（全カット自動採点・API コストゼロ）
Stage 6: Lightroom 用メタデータ書き出し（XMP サイドカー）  ←  ここがゴール
```

### 各ステージの詳細

| Stage | 実行場所 | 内容 |
|-------|---------|------|
| 0 | Claude | 撮影意図・フォルダパスのヒアリング |
| 1 | ローカル (OpenCV) | 白飛び 80% 以上 / 黒潰れ 80% 以上のみ絶対除外 |
| 2 | ローカル (OpenCV + MediaPipe) | EXIF で連写グループ化、技術スコア算出、HTML ダッシュボード生成 |
| 3 | ローカル + ブラウザ | 代表カット（最大 50 枚）を人間がブラウザ UI でレーティング |
| 4 | Claude（ネイティブ） | レーティング結果から「審美眼プロファイル」を言語化 |
| 5 | ローカル (CLIP) | プロファイルを使って全カットを自動採点 |
| 6 | ローカル (ExifTool) | 採点結果を Lightroom 用 XMP に書き出し |

**人間の介入は 2 回だけ:**
1. 撮影意図を伝える（Step 0）
2. 代表カットをレーティングする（Step 3、約 6 分）

あとは Claude が全て自律的に進める。

---

## Quick Start

```bash
# 1. セットアップ（初回のみ）
bash plugins/aesthetic-shadowing/stage1/setup.sh

# 2. Claude Code で写真セレクトを開始
/aesthetic-shadowing:photo-selector
```

Claude が撮影意図・フォルダパスを聞くので答えるだけで、Stage 0〜6 まで自律実行する。

### 手動実行する場合

```bash
VENV=plugins/aesthetic-shadowing/stage1/.venv/bin/python
JPEG_DIR=/path/to/photos/JPEG
OUTPUT_DIR=/path/to/photos

# Stage 1: 技術フィルタリング
$VENV plugins/aesthetic-shadowing/stage1/analyze.py $JPEG_DIR $OUTPUT_DIR/xmp_out/

# Stage 2: グループ化 + ダッシュボード
$VENV plugins/aesthetic-shadowing/stage2/group.py $JPEG_DIR \
  --xmp-dir $OUTPUT_DIR/xmp_out/ \
  --output $OUTPUT_DIR/stage2_groups.csv

# ブラウザでダッシュボードを開く
open $OUTPUT_DIR/stage2_report.html
```

---

## Requirements

- **Python 3.11+**
- **ExifTool** — `brew install exiftool`（macOS）
- **Claude Code** — `npm install -g @anthropic-ai/claude-code`

Python 依存パッケージは `setup.sh` が自動インストールする（venv 作成含む）。

Stage 5 の CLIP モデル（約 340 MB）は初回実行時に自動ダウンロード。Apple Silicon Mac では MPS が自動有効化される。

---

## 出力ファイル

| ファイル | 内容 |
|----------|------|
| `stage2_report.html` | グループ別サムネイル + 技術スコア（Stage 3 の前確認用） |
| `stage2_groups.csv` | グループ化・スコア付き CSV |
| `aesthetic_profile.json` | Claude 生成の審美眼プロファイル |
| `batch_scores.csv` | CLIP バッチスコアリング結果（全カット） |
| `*.xmp` / `*.JPG` | 星レーティング書き込み済み（Lightroom 連携） |

---

## 審査員向け（AGI Labo ハッカソン 2026-3）

### 自律性のポイント

Claude Code のスキルシステムを使い、`/aesthetic-shadowing:photo-selector` の一言から Stage 6 の書き出しまでを **Claude が自律実行**する。

- **LLM の使いどころを絞る**: Stage 1〜2 は純粋なローカル処理（OpenCV / MediaPipe）。Claude は Stage 4 の「審美眼言語化」と Stage 5 の判断基準設計にのみ使う。API コストを最小化しながら、人間の審美眼を再現する。
- **Human-in-the-Loop の設計**: 「全自動より、2 回だけ人間が関わる設計の方が信頼できる」という思想。介入点を明示することで、ユーザーは自分の審美眼が反映されていると感じる。
- **暗黙知のアルゴリズム化**: 「連写の最初の 1 枚は意図的な 1 枚」「最後の 1 枚は調整後の決定打」という撮影者の暗黙知をグループ化アルゴリズムに組み込んでいる。

### 技術的な工夫

- Stage 1 でピンボケ除外を**廃止**した。屋内イベント撮影では低コントラストのせいでラプラシアン分散が低くなり、90% 超除外という誤爆が起きていた。除外対象を「完全白飛び / 黒潰れ」のみに絞り、0.3% の除外率を実現した。
- Stage 5 は CLIP をローカル実行するため、1,000 枚をスコアリングしても **API コストゼロ**。
- Stage 6 は JPEG 直接書き込みと RAW 用 XMP サイドカー生成を自動判別するハイブリッド方式。

---

## ディレクトリ構成

```
plugins/aesthetic-shadowing/
├── stage0/          # セッションブリーフ生成
├── stage1/          # 技術フィルタリング + .venv
├── stage2/          # グループ化 + HTML ダッシュボード
├── stage3/          # ブラウザ審美眼サンプリング UI
├── stage4/          # 審美眼プロファイル生成（Claude）
├── stage5/          # CLIP バッチスコアリング
├── stage6/          # XMP / メタデータ書き出し
├── skills/
│   ├── photo-selector/   # Claude Code スキル定義
│   └── chronicler/       # 開発記録スキル
├── MY_JOURNEY.md    # 開発ログ（chronicler 生成）
└── README.md        # このファイル
```

---

*Built with Claude Code — AGI Labo Skills Marketplace*
