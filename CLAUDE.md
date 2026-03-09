# Aesthetic Shadowing Agent — プロジェクトコンテキスト

## 目的
フォトグラファーのRAWデータセレクト作業を自律的に代行するAIエージェント。
「プロの審美眼」を対話で学習し、Lightroom作業前のレーティングを完成させる。

## 設計の3原則
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
