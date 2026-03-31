# フィールドレポート — 実案件での初実戦 (2026-04-01)

**セッション**: colorier 店舗イメージ (2026-03-31撮影)
**規模**: 839枚 / 373グループ / 3拠点（表参道店・bio mart colorier・代官山店）
**報告者**: Claude Code (photo-selectorスキル実行エージェント)

---

## 総評

パイプライン自体は完走した。しかし**「2ndセレクトが苦行でなく楽しみになる」というゴール**には届いていない。主要因は2つ：judge.pyをスキップしたことによる学習データの欠陥、およびプロファイルの「露出オーバー許容」指示がCLIPの判定を緩めすぎたこと。

---

## 課題 1: judge.pyスキップが引き起こした代表性の崩壊（設計上の最重要課題）

### 何が起きたか
ユーザーが時間を節約するためjudge.pyをスキップし、ShutterSnitch現場レーティング（42枚）をrated_samples.jsonに変換して代替した。

### 問題の構造
| 項目 | 本来のjudge.py | ShutterSnitchによる代替 |
|---|---|---|
| サンプリング元 | 373グループ全体から代表カット | 現場で目についたカット（偶然の42枚）|
| グループカバー率 | 〜100%（グループ数の10%=37枚） | 11%（42/373グループ） |
| high/low バランス | 自然に均衡（〜50%/50%） | high=4枚 / low=31枚（1:7.75の偏り）|
| 時間帯の代表性 | 全撮影時間帯から選出 | 特定シーンに集中 |

### 結果として生じた判定の歪み
- **代官山店（14:00〜）のカット105枚中78枚が★0**になった
- 原因: high評価サンプルが代官山店から`_A6A0099.JPG`の1枚のみ
- CLIPが「代官山店のスタイル」を学習できず、大半を低スコア判定

### 提案: ShutterSnitch/既存レーティングのインポート機能

judge.pyに`--import-ratings`オプションを追加することを提案する：

```
python judge.py <jpeg_dir> --csv stage2_groups.csv \
  --import-ratings <ShutterSnitchCSV or XMPdir> \
  --rating-map "1:2,2:3,3:5"   # ★→1-5スケールへのマッピング定義
```

インポート時は**グループカバレッジを優先**して不足グループを自動補完する設計が望ましい。
「現場レーティング済み = 完了」ではなく「現場レーティング = ヒント付きでjudge.pyを実行」として扱う。

---

## 課題 2: 露出オーバー許容指示がCLIPの判定を過度に緩めた（プロファイル設計の問題）

### 何が起きたか
ユーザーは「露出不足は現像で回復可能なので許容、ピンボケのみ除外」という意図をプロファイルに記述した。しかしCLIPはこの指示を**「露出オーバーなカットも採用候補に含める」**方向に解釈し、本来であれば除外すべきカットに★1〜★2を付与した。

### 結果
- ★2以上（採用候補）が199枚（23.7%）
- ユーザーの期待値: 「1stセレクト後に★0を50%以上にしたい」→ 実態は★0が15.5%（130枚）のみ
- 2ndセレクトのクライアントに渡せる「素敵な写真のみで構成された候補集」にならなかった

### 問題の根本
**Stage1の閾値設計とプロファイルの「許容」指示が衝突している。**

Stage1は「80%以上の白飛び・黒潰れ」のみ除外する設計（意図的に寛容）。一方プロファイルで「オーバーでも許容」と明示されると、Stage5 CLIPが「オーバー傾向のカットも正例とみなす」可能性がある。

### 提案: 「採用率ターゲット」パラメータの導入

```python
# judge.py または session.json に追加
"target_rejection_rate": 0.5  # 最低50%を★0にしたい
```

このパラメータをStage5のthreshold決定に反映させる：
- 現在: `--thresholds "0.25,0.50,0.75"`（固定）
- 改善後: ターゲット採用率から逆算して閾値を動的決定

また、Stage4のプロファイル生成時に「露出許容」という指示が含まれた場合、**CLIPへの渡し方を調整**する（low_keywordsに「重度オーバー露出」を明示追加するなど）。

---

## 課題 3: batch_scores.csv と xmp_writer.py のカラム名不一致（バグ）

### 何が起きたか
Stage5（score.py）が出力するCSVのヘッダーは `filename` だが、Stage6（xmp_writer.py）は `file` を参照している。

```
batch_scores.csv の実際のヘッダー:
  filename, clip_score, high_score, low_score, composite_score, star_rating

xmp_writer.py のコード:
  filename = row.get("file", "")  # → 空文字列を返す
```

### 影響
- **xmp_writer.pyが全839件をエラー判定**（エラー=839）
- 本番環境では`exiftool`による書き込みが一切行われない
- 今回は`sed`でCSVヘッダーを修正して回避した

### 修正方法
```python
# xmp_writer.py
filename = row.get("file") or row.get("filename", "")
```
または、score.pyのCSV出力カラム名を`file`に統一する。

---

## 課題 4: `exiftool -ext JPG -o <dir>/` がXMPではなくJPEGをコピーする（使用法の罠）

### 何が起きたか
XMPサイドカーを生成しようとして以下を実行した：
```bash
exiftool -ext JPG -o "$xmp_dir/" "$jpeg_dir"
```
これはJPEGファイル自体をxmp_dirにコピーした（839枚のJPEGがXMPフォルダに混入）。

### 正しい使用法
```bash
exiftool -ext JPG -o "$xmp_dir/%f.xmp" "$jpeg_dir"
```
`%f.xmp`を指定することでXMPサイドカーとして出力される。

### 提案
skillのプロンプトに正しいexiftoolコマンドを明記する（現在は記述なし）。

---

## 課題 5: スキルプロンプトの xmp_dir の役割が曖昧

### 何が起きたか
skillのStep 1では `xmp_dir` を「Stage1のXMP出力先」として定義しているが、Stage6の`xmp_writer.py`は`--xmp-dir`オプションを「使用しません」と明示。XMP最終出力先とStage1の中間出力先が同一変数を共有しているため混乱が生じた。

### 提案
- `stage1_xmp_dir`（Stage1の技術フィルタ結果XMP格納先）
- `final_xmp_dir`（最終成果物のXMPサイドカー格納先・ユーザー指定）
を変数として分離する。

---

## 追加観察: _A6A0099.JPGのCLIPスコア低下

ShutterSnitch★★★（最高評価）の`_A6A0099.JPG`のCLIPスコアが低かった（composite=0.509）。
原因として**縦位置JPEGのExif Orientation未考慮**が疑われる（画像が90度回転した状態でCLIPに入力された可能性）。
CLIPはOrientation EXIFを無視するため、縦位置カットのスコアが系統的に低下しうる。
→ Stage5の前処理でExif Orientationに基づく画像回転を実施することを推奨。

---

## まとめ: 優先度別の改善項目

| 優先度 | 課題 | 分類 |
|---|---|---|
| **P0** | batch_scores.csv と xmp_writer.py のカラム名不一致 | バグ修正 |
| **P0** | 採用率ターゲットパラメータの導入 | 設計 |
| **P1** | judge.py への`--import-ratings`オプション追加 | 機能追加 |
| **P1** | Stage5のExif Orientation対応 | バグ修正 |
| **P2** | `xmp_dir`変数の分離（stage1用/final用） | 設計 |
| **P2** | exiftoolのXMP生成コマンドをskillプロンプトに明記 | ドキュメント |

---

*このレポートは 2026-04-01 の colorier 店舗イメージセッション（839枚）での実戦から得られたフィードバックです。*
