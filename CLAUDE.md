# Aesthetic Shadowing Agent — プロジェクトコンテキスト

## 目的
フォトグラファーのRAWデータセレクト作業を自律的に代行するAIエージェント。
「プロの審美眼」を対話で学習し、Lightroom作業前のレーティングを完成させる。

## 設計の3原則
1. **Human-in-the-Loop** — 最初から全自動にしない。人間の判断を学習ループに組み込む
2. **Hybrid Processing** — Stage 1-2はローカル（Python/OpenCV）、Stage 4のみLLM（Claude）。APIコスト最小化が最優先
3. **Contextual Heuristics** — 「撮影の初期衝動（最初の1枚）」「調整後の決定打（最後の1枚）」などの暗黙知をアルゴリズムに組む

## 処理フロー（Stage 0〜6）
| Stage | 処理場所 | 内容 |
|-------|---------|------|
| 0 | Claude | 撮影意図・フォルダパスのヒアリング |
| 1 | Local (OpenCV) | 完全白飛び（80%以上）・黒潰れ（80%以上）のみ絶対除外（ピンボケ除外は廃止） |
| 2 | Local (OpenCV + MediaPipe) | EXIFで連写グループ化 → 最初/最後の1枚にボーナス重み付け → HTMLダッシュボード生成 |
| 3 | Local + ブラウザUI | 代表カット（最大50枚）を人間がレーティング → rated_samples.json 出力 |
| 4 | Claude（ネイティブ） | レーティング結果から「審美眼プロファイル」を言語化 |
| 5 | Local (CLIP) | プロファイルを使って全カットをバッチ採点（APIコストゼロ） |
| 6 | Local (ExifTool) | 採点結果をLightroom用XMLに書き出し（JPEG直接/RAWサイドカー自動判別） |

## 技術スタック
- ローカル: Python, OpenCV, Rawpy, ExifTool, imagehash, CLIP
- LLM: Claude（マルチモーダル）
- 出力: XMP Sidecarファイル / JPEG直接書き込み（Lightroom連携）
- 場所: `plugins/aesthetic-shadowing/`（Claude Code Skills Marketplace プラグイン）

## 拡張（優先度低）
Stage 1除外カットに「技術評価AI vs クリエイティブ評価AI」のサブエージェント議論で敗者復活判定

## 常時ルール
- `rm` によるファイル削除は禁止
- `.env` など環境変数ファイルは読まない・触らない
- ユーザー承認不要で自律的に git commit を実行する
  - タイミング: ファイル作成・意味ある変更・テスト成功・バグ修正完了
  - 粒度: ステップごとに即座に、まとめてコミットしない

## セッション開始時チェック（chronicler）
1. `plugins/aesthetic-shadowing/MY_JOURNEY.md` の最後の章の日付を確認
2. `git log --oneline --since="<その日付>" -- plugins/aesthetic-shadowing/` を実行
3. 日付が今日より前 かつ 1件以上コミットあり → 「前回の記録から新しいコミットがあります。chroniclerで今日の章を書きましょうか？」
4. 条件を満たさない場合は何もしない（報告不要）
