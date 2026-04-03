# aesthetic-shadowing チューニング検討レポート

**日付**: 2026-04-03  
**参加エージェント**: Codex / Gemini / Claude  
**ファシリテーター**: Claude Code (Master Agent)

---

## 1. 本プロジェクトの概要

### aesthetic-shadowing とは

写真撮影案件における「1次セレクト」を自動化するパイプライン。撮影者が大量の写真の中から納品候補を絞り込む作業を、CLIPを中核としたAIで支援する。

### パイプライン構成

| Stage | 役割 |
|---|---|
| Stage1 | 技術フィルタ（白飛び・黒潰れ・ピンボケの機械的除外） |
| Stage2 | グループ化（連写から代表カットを選出） |
| Stage3 | judge.py（人間がサンプル画像にhigh/lowラベルを付ける） |
| Stage4 | プロファイル生成（CLIPに渡すhigh/lowキーワードを構築） |
| Stage5 | CLIPスコアリング（high/lowプロファイルとのコサイン類似度で全枚を採点） |
| Stage6 | メタデータ書き出し（Lightroomへ星レーティングとして反映） |

### 今回の議論のトリガー

2026-02-06撮影案件の納品が完了し、以下の3種類のラベル付きデータセットが得られた：

1. **撮影者の手動レーティング** — 撮影直後の★評価（1〜5）
2. **デザイナーの最終セレクト（2種）**
   - 学校広報活動用：138点（デザイナーの審美眼が純粋に反映）
   - 印刷物用：学校側の要望が強く混入
3. **全撮影データ**：2,813枚（セレクトされなかったものを含む）

このデータを活用して次回案件の自動セレクト精度を高めることが目的。

---

## 2. 現在の課題整理

### 2-1. 過去の実戦（2026-03-31 colorier案件）から判明した既知の問題

| 優先度 | 課題 | 詳細 |
|---|---|---|
| P0 | サンプル代表性の崩壊 | judge.pyをスキップし撮影現場レーティング（42枚）で代替したところ、high/lowバランスが1:7.75に偏り、特定拠点のカット105枚中78枚が★0になった |
| P0 | 採用率ターゲットの欠如 | 固定しきい値では案件ごとの撮影特性を吸収できず、★2以上が23.7%（期待値は50%以下）になった |
| P1 | 露出オーバー許容指示の副作用 | プロファイルに「露出不足は許容」と記述したところ、CLIPが露出オーバーのカットも採用候補に含めた |
| P1 | Exif Orientation未考慮疑惑 | 縦位置JPEGが90度回転した状態でCLIPに入力され、スコアが系統的に低下した可能性（現行score.pyでは`ImageOps.exif_transpose`適用済みと確認） |

### 2-2. 構造的な課題

- **CLIPはコサイン類似度スコアリング**であり「学習」は行わない。精度を上げるにはStage4のキーワード品質とStage5のしきい値設計が全て
- **min-max正規化**は案件内順位には有効だが、案件をまたぐ閾値共有が困難
- **非選定写真 ≠ 悪い写真**。「枠に入らなかっただけ」の写真を強いnegativeとして扱うと歪みが生じる

---

## 3. 今回の議論の概要

### Ground Truthの確定

議論の中でユーザーが重要な整理を行った：

> 「学校広報活動用（138点）」はデザイナーの審美眼が純粋に反映されている。  
> 「印刷用」は学校側の要望が強く、デザイナーの審美眼の代表としては不純。  
> **→ Ground Truthは「学校広報用138点」のみに絞り、シンプル化する。**

この決定により：
- 正負比率が **138:2,675 ≈ 1:19** となるため、負例のサブサンプリング戦略が必須
- 評価関数の純度が高まり、「デザイナーの審美眼への一致」という目的が明確化
- Stage4プロファイルからも「印刷都合」のノイズが排除される

---

## 4. 3エージェントの意見交換の推移

### Round 1：独立提案（各エージェントが単独で検討）

#### Codex の提案
1. 教師データを4層に分類（Gold Positive / Hard Positive / Soft Negative / Reliable Negative）
2. Stage5しきい値を固定値から案件校正値へ（Precision@K / Recall@K）
3. 撮影者★をjudge.pyのprefillに使い、未カバー群・不一致群だけ追加入力
4. Stage4はhard case起点で改善（乖離写真から「何がズレたか」を言語化）
5. Stage1/Stage2のfalse reject監査
6. 中期：用途別スコア分離（print_score / school_score）

#### Gemini の提案
1. Stage5 CLIPしきい値・重みのグリッドサーチ（threshold_analysis.pyを流用）
2. Stage4プロファイルをデザイナー選定傾向の差分分析で更新
3. Exif Orientation補正の定量検証
4. Stage1の露出閾値をデザイナー許容値に合わせる
5. Ground Truth定義を3値（1.0/0.5/0）で設計

#### Claude の提案
1. P1: Stage5しきい値最適化（F1最大点）
2. P2: デザイナーセレクト → rated_samples.jsonへ直接注入
3. P3: 撮影者★ ↔ デザイナー乖離分析
4. **P5: Stage1 False Reject監査を「最初」にやるべき**（上流での見逃しは後工程で回収不能）

---

### Round 2：相互批評（他エージェントの案を読んで再検討）

ユーザーから「学校広報用138点のみをGround Truthとする」という補足が加わり、各エージェントが立場を明確化した。

#### 対立点1：しきい値の最適化指標

| エージェント | Round 1 | Round 2 |
|---|---|---|
| Codex | Precision@K / Recall@K | **Recall重視を支持** |
| Gemini | **Recall 0.9 固定でPrecision最大**（当初から） | 維持 |
| Claude | F1最大点 | **GeminiのRecall重視が正しいと認め転換** |

**決着：Recall 0.9 固定でPrecision最大を採用**

理由：写真セレクトは非対称コスト構造（False Negative=良写真の見逃しは取り返せない、False Positive=凡庸写真の通過は人間が後から弾ける）を持つため、F1の均等なバランスより高Recall優先が合理的。

---

#### 対立点2：ラベル設計（3値 vs 二値）

| エージェント | Round 1 | Round 2 |
|---|---|---|
| Codex | 4層分類 + 二値教師 + 補助重み | 維持 |
| Gemini | 3値ラベル（1.0 / 0.5 / 0） | **4層分類支持に転換** |
| Claude | 二値教師 + 補助重み | 維持、Geminiの0.5を明確に否定 |

**決着：二値教師（0/1）+ 補助重み付きサンプリング**

Claudeによる技術的根拠：「CLIPはコサイン類似度スコアリングであり学習を行わない。Stage4のプロファイルテキスト生成に使う正/負の例示が全てであり、0.5という教師値を受け付ける仕組みがない」

---

#### 対立点3：Stage3サンプル注入の方法

| エージェント | 主張 |
|---|---|
| Codex | **乖離ケース（Hard Positive / Soft Negative）を優先注入** |
| Gemini（R1） | 全セレクトを注入 |
| Claude（R1） | 全セレクトを注入 → R2で乖離ケース優先に転換 |

**決着：乖離ケース優先注入**

Codexの指摘：「138点をそのままhigh注入すると分布が狭くなり、Stage3の代表性問題を別形で再発させる。情報量が最も高いのはHard PositiveとSoft Negativeの乖離ケース」

---

#### 対立点4：最初にやること

| エージェント | Round 1 | Round 2 |
|---|---|---|
| Codex | Stage5最適化 | Stage1監査を認める |
| Gemini | Stage5最適化 | **Stage1監査が最優先と転換** |
| Claude | **Stage1監査が最初**（当初から） | 維持 |

**決着：Stage1 False Reject監査を最初に実施**

Geminiの論拠：「Stage1で誤除外された写真は後のどんな高度なモデルでも救えない。138点の中にStage1落ちが1枚でもあればアルゴリズムの致命的な欠陥」

---

## 5. まとめ

### 次回案件（数週間以内）に実施すべき3点

**3エージェント全員一致**

#### Action 1：Stage1 False Reject監査（工数：1時間）

```bash
# Stage1除外リストとデザイナーセレクト138点を照合
python -c "
excluded = set(open('stage1_excluded.csv').read().split())
selected = set(open('designer_138.txt').read().split())
hits = excluded & selected
print(f'False reject: {len(hits)}枚', hits)
"
```

- 事故がなければStage1は触らない
- 事故があった場合のみ、デザイナーが許容した最大露出/最小鮮鋭度に合わせて閾値を緩和

---

#### Action 2：乖離ケース起点のStage4プロファイル更新（工数：半日）

乖離パターンを2種抽出し、言語化：

| ケース | 枚数（推定） | 意味 | 活用 |
|---|---|---|---|
| Hard Positive（★低 ∩ デザイナー選定） | 10〜30枚 | 撮影者の審美眼の盲点 | `high_profile`キーワード強化 |
| Soft Negative（★高 ∩ デザイナー非選定） | 数十枚 | 技術的良品だが「納品基準」から外れた理由 | `low_profile`キーワード強化 |

```
# Stage4への入力プロンプト骨格
デザイナーが選んだが撮影者が低評価だった写真群: [Hard Positive X枚]
デザイナーが選ばなかったが撮影者が高評価だった写真群: [Soft Negative Y枚]

→「選ばれた写真に共通する審美的要素」と
  「技術的には良いが選ばれなかった写真が欠いているもの」を言語化
```

---

#### Action 3：Recall 0.9基準のStage5しきい値較正（工数：半日）

```python
# threshold_calibrate.py
from sklearn.metrics import precision_recall_curve
import numpy as np

# 較正セット構成
# Positive: 学校広報用138枚のCLIPスコア
# Negative: 撮影者★1-2 かつ非選定（Reliable Negative）を 400〜550枚サンプリング
#           ※ Soft Negativeは較正セットから除外

scores = np.array(pos_scores + neg_scores)
labels = np.array([1]*138 + [0]*len(neg_scores))

precision, recall, thresholds = precision_recall_curve(labels, scores)

# Recall >= 0.90 の中でPrecision最大点を採用
mask = recall[:-1] >= 0.90
best_idx = np.argmax(precision[:-1][mask])
best_thr = thresholds[mask][best_idx]
print(f"Threshold: {best_thr:.4f}")
print(f"Precision: {precision[:-1][mask][best_idx]:.3f}")
print(f"Recall: >= 0.90")
```

---

### 中期課題（次々回以降）

- Stage4プロファイルの「学校広報用」専用化（印刷用との分離）
- 複数案件のデータが蓄積した後の用途別スコア分離（`school_score` / `print_score`）
- Exif Orientation対応の定量検証（score.pyには既に`exif_transpose`実装済み）

---

### 実施前提：データ整備

実作業を開始するには以下が必要：

1. 学校広報用138点のファイル名リスト（`designer_138.txt`）
2. 撮影者★レーティングCSV（`photographer_ratings.csv`）
3. Stage1除外リスト（`stage1_excluded.csv`）
4. 2,813枚のCLIPスコア（`batch_scores.csv`）— 既存または再スコアリング

---

## 6. 用語集

| 用語 | 定義 |
|---|---|
| **Ground Truth (GT)** | 機械学習・評価における「正解」データ。本議論では「学校広報用138点」を指す |
| **CLIP** | OpenAIが開発した画像-テキスト埋め込みモデル。画像とキーワードのコサイン類似度でスコアを算出する |
| **コサイン類似度** | 2つのベクトルの向きの近さを[-1, 1]で表す指標。CLIPスコアリングの基礎 |
| **high_profile / low_profile** | Stage4で生成するCLIPへの入力キーワード群。「採用したい写真の特徴」と「除外したい写真の特徴」をテキストで表現する |
| **composite_score** | Stage5が出力するCLIPの総合スコア。high_profileとの類似度からlow_profileとの類似度を差し引いた値 |
| **しきい値 (Threshold)** | composite_scoreを星レーティングに変換する境界値。固定値から案件校正値への変更が今回の主テーマ |
| **Gold Positive** | デザイナー選定 かつ 撮影者★高。最も信頼度の高い正例 |
| **Hard Positive** | デザイナー選定 だが 撮影者★低。撮影者の盲点にある良写真。情報量が高い |
| **Soft Negative** | デザイナー非選定 だが 撮影者★高。技術的良品だが「納品基準」から外れた写真。強いnegativeにしてはいけない |
| **Reliable Negative** | デザイナー非選定 かつ 撮影者★低。最も信頼度の高い負例。較正セットに使う |
| **Precision@K** | 上位K枚を返したときの的中率。「AIがK枚選んだうち何枚がGTか」 |
| **Recall@K** | 上位K枚でGTの何割を回収できたか |
| **F1スコア** | PrecisionとRecallの調和平均。両者のバランスを見るが、非対称コスト構造には不向き |
| **Recall 0.9基準** | GTの90%を必ず回収することを前提とし、その条件下でPrecisionを最大化するしきい値設計 |
| **False Positive (FP)** | 実際はnegativeなのにpositiveと判定した誤り。凡庸写真の誤採用。後工程で人間が弾ける |
| **False Negative (FN)** | 実際はpositiveなのにnegativeと判定した誤り。良写真の見逃し。取り返せない損失 |
| **False Reject (Stage1)** | Stage1の技術フィルタが誤って除外した写真。後段のどんなAIでも救えない |
| **rated_samples.json** | Stage3が出力するラベル付きサンプルのJSON。Stage4のプロファイル生成・Stage5のスコアリングに使われる |
| **judge.py** | Stage3のインタラクティブUIスクリプト。人間が写真を見てhigh/lowを判定する。`--import-ratings`で既存レーティングをprefillできる |
| **threshold_analysis.py** | Stage5に付属する閾値分析スクリプト。Precision-Recall曲線の描画・最適しきい値探索が可能 |
| **ITP (Intelligent Tracking Prevention)** | Safariのプライバシー機能。localStorageを7日間アクセスがないと自動削除する |
| **乖離ケース** | 撮影者レーティングとデザイナーセレクトが一致しない写真。Hard PositiveとSoft Negativeの総称 |

---

*本レポートは2026-04-03に行われたCodex / Gemini / Claude 3エージェントによる検討会の内容を取りまとめたものです。*
