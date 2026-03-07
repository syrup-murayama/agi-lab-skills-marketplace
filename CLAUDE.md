# Aesthetic Shadowing Agent — プロジェクトコンテキスト

## 目的
フォトグラファーのRAWデータセレクト作業を自律的に代行するAIエージェント。
「プロの審美眼」を対話で学習し、Lightroom作業前のレーティングを完成させる。

## 設計の3原則（判断に使う軸）
1. **Human-in-the-Loop** — 最初から全自動にしない。人間の判断を学習ループに組み込む
2. **Hybrid Processing** — Stage 1-2はローカル（Python/OpenCV）、Stage 3-4のみLLM。APIコスト最小化が最優先
3. **Contextual Heuristics** — 「撮影の初期衝動（最初の1枚）」「調整後の決定打（最後の1枚）」などの暗黙知をアルゴリズムに組む

## 処理フロー（4ステージ）
| Stage | 処理場所 | 内容 |
|-------|---------|------|
| 1 | Local | ヒストグラム・ラプラシアン分散でピンボケ/白飛び/黒潰れを除外 |
| 2 | Local | EXIFで連写グループ化 → 最初/最後の1枚にボーナス重み付け |
| 3 | LLM | 代表カットのA/B判定をユーザーに提示 → フィードバックで審査基準を動的更新 |
| 4 | LLM | 確定ルールで残り全カットをバッチ評価 → XMP Sidecar書き出し |

## 技術スタック
- ローカル: Python, OpenCV, Rawpy, ExifTool
- LLM: Claude（マルチモーダル）/ Gemini 1.5 Pro
- 出力: XMP Sidecarファイル（Lightroom連携）

## 拡張（優先度低）
Stage 1除外カットに「技術評価AI vs クリエイティブ評価AI」のサブエージェント議論で敗者復活判定

## このrepoの位置づけ
Claude Code Skills Marketplace のプラグインとして開発中。
`plugins/aesthetic-shadowing/` 以下に実装を置く想定。

## セッション開始時の自動チェック（chronicler）

**毎回セッションの開始時に以下を必ず実行すること。**

1. `plugins/aesthetic-shadowing/MY_JOURNEY.md` を読み、最後の章の日付を確認する
2. `git log --oneline --since="<その日付>" -- plugins/aesthetic-shadowing/` を実行し、その日付以降の新規コミットがあるか確認する
3. 以下の条件を**両方**満たす場合、ユーザーに声をかける：
   - 最後の章の日付が今日より前である
   - 上記コマンドで1件以上のコミットが見つかった
4. 声のかけ方：「前回の記録から新しいコミットがあります。chroniclerで今日の章を書きましょうか？」
5. 条件を満たさない場合は何もしない（ユーザーに報告不要）
