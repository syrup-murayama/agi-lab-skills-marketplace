#!/usr/bin/env python3
"""
Delivery Review ツール v2

採点済みCSVとJPEGフォルダから納品レビュー用フォルダを生成する。
生成されたフォルダをNetlify Dropにドラッグするだけで共有できる。

フォルダ構成:
  <output>/
    index.html        ← 画像は相対パスで参照
    thumbs/           ← 長辺300px JPEG Q=70（グリッド表示用）
    preview/          ← 長辺1500px JPEG Q=85（ライトボックス表示用）

使い方:
  python3 report_client.py \\
    --jpeg-dir /path/to/jpegs \\
    --output ./delivery_review \\
    --client "○○学校" \\
    --email syrup.murayama@gmail.com \\
    --csv /path/to/batch_scores.csv \\
    --target 100
"""

import argparse
import csv
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillowがインストールされていません: pip install Pillow", file=sys.stderr)
    sys.exit(1)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="納品レビューフォルダを生成する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--jpeg-dir", required=True, metavar="DIR",
                   help="JPEGフォルダのパス")
    p.add_argument("--output", required=True, metavar="DIR",
                   help="出力フォルダのパス（フォルダとして作成）")
    p.add_argument("--client", default="クライアント", metavar="NAME",
                   help="クライアント名")
    p.add_argument("--email", default="", metavar="EMAIL",
                   help="納品先メールアドレス")
    p.add_argument("--csv", default="", metavar="FILE",
                   help="batch_scores.csv のパス（省略時は全JPEG均等扱い）")
    p.add_argument("--target", type=int, default=0, metavar="N",
                   help="目標納品枚数（0=制限なし）")
    p.add_argument("--no-ai-rating", action="store_true",
                   help="AIレーティングを非表示にする（クライアント向けバイアスなし版）")
    return p.parse_args()


# ─── EXIF ─────────────────────────────────────────────────────────────────────

def fetch_exif_data(jpegs: list[Path]) -> dict:
    """exiftoolで全ファイルの XMP:Rating, DateTimeOriginal を一括取得。
    exiftool が無い場合や失敗時は空 dict を返す（エラーにしない）。"""
    if not jpegs:
        return {}
    try:
        result = subprocess.run(
            ['exiftool', '-j', '-XMP:Rating', '-EXIF:DateTimeOriginal']
            + [str(j) for j in jpegs],
            capture_output=True, text=True, timeout=120,
        )
        # exiftool returns 1 when some files had minor issues — still parse output
        data = json.loads(result.stdout)
        return {Path(item['SourceFile']).name: item for item in data}
    except (FileNotFoundError, json.JSONDecodeError, Exception):
        return {}


# ─── グルーピング ─────────────────────────────────────────────────────────────

def assign_groups(photos: list[dict]) -> None:
    """DateTimeOriginal を元に 5 分以内の連続ショットを同一 group_id として付与"""
    FMT = '%Y:%m:%d %H:%M:%S'

    dated: list[tuple[int, datetime]] = []
    for i, p in enumerate(photos):
        dt_str = p.get('datetime_str', '')
        if dt_str:
            try:
                dated.append((i, datetime.strptime(dt_str, FMT)))
            except ValueError:
                pass

    dated.sort(key=lambda x: x[1])

    group_id = 0
    prev_dt: datetime | None = None
    for i, dt in dated:
        if prev_dt is None or (dt - prev_dt).total_seconds() > 300:
            group_id += 1
        photos[i]['group_id'] = group_id
        prev_dt = dt

    # datetime なし → 個別グループ
    next_group = group_id + 1
    for p in photos:
        if 'group_id' not in p:
            p['group_id'] = next_group
            next_group += 1


# ─── データ読み込み ───────────────────────────────────────────────────────────

def load_scores(csv_path: str) -> dict:
    """batch_scores.csv を読み込み {filename: {star_rating, composite_score}} を返す"""
    scores: dict = {}
    if not csv_path:
        return scores
    p = Path(csv_path)
    if not p.exists():
        print(f"WARNING: CSVが見つかりません: {csv_path}", file=sys.stderr)
        return scores
    with open(p, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            scores[row['filename']] = {
                'star_rating': int(row.get('star_rating', 0)),
                'composite_score': float(row.get('composite_score', 0.0)),
            }
    return scores


def find_jpegs(jpeg_dir: Path) -> list[Path]:
    result: list[Path] = []
    for ext in ('*.JPG', '*.jpg', '*.jpeg', '*.JPEG'):
        result.extend(jpeg_dir.glob(ext))
    return sorted(set(result), key=lambda x: x.name)


def build_photo_list(jpegs: list[Path], scores: dict) -> list[dict]:
    """star_rating 降順 → composite_score 降順でソートした写真リストを返す"""
    exif_data = fetch_exif_data(jpegs)

    photos = []
    for src in jpegs:
        name = src.name
        sc   = scores.get(name, {})
        exif = exif_data.get(name, {})
        photos.append({
            'filename':       name,
            'thumb_name':     src.stem + '.jpg',
            'star_rating':    sc.get('star_rating', 0),
            'composite_score': sc.get('composite_score', 0.0),
            'initial_rating': int(exif.get('XMP:Rating', 0) or 0),
            'datetime_str':   exif.get('EXIF:DateTimeOriginal', '') or '',
        })

    photos.sort(key=lambda x: (-x['star_rating'], -x['composite_score']))
    assign_groups(photos)
    return photos


# ─── 画像生成 ─────────────────────────────────────────────────────────────────

def make_resized(src: Path, dst: Path, max_px: int, quality: int) -> None:
    """既存ファイルはスキップ、新規のみリサイズ保存する"""
    if dst.exists():
        return
    with Image.open(src) as img:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        img.convert('RGB').save(dst, 'JPEG', quality=quality, optimize=True)


def generate_images(jpegs: list[Path], jpeg_dir: Path, out_dir: Path) -> None:
    thumbs_dir  = out_dir / 'thumbs'
    preview_dir = out_dir / 'preview'
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    total = len(jpegs)
    for i, src in enumerate(jpegs, 1):
        stem = src.stem
        make_resized(src, thumbs_dir  / (stem + '.jpg'), 300,  70)
        make_resized(src, preview_dir / (stem + '.jpg'), 1500, 85)
        if i % 20 == 0 or i == total:
            print(f"\r  画像生成中: {i}/{total}", end='', file=sys.stderr, flush=True)
    print(f"\r  完了: thumbs/preview を {total} 枚生成            ", file=sys.stderr)


# ─── HTML生成 ─────────────────────────────────────────────────────────────────

def generate_html(photos: list[dict], client: str, email: str, target: int,
                  storage_key: str, storage_key_ratings: str,
                  show_ai_rating: bool = True) -> str:
    target_str = str(target) if target > 0 else '─'
    total      = len(photos)

    photo_data_js = json.dumps(
        [{
            'filename':      p['filename'],
            'thumbName':     p['thumb_name'],
            'groupId':       p.get('group_id', 0),
            'initialRating': p.get('initial_rating', 0) if show_ai_rating else None,
            'datetime':      p.get('datetime_str', ''),
            'starRating':    p.get('star_rating', 0),
            'compositeScore': p.get('composite_score', 0.0),
        } for p in photos],
        ensure_ascii=False,
    )

    progress_html = (
        '<div class="progress-wrap">'
        '<div class="progress-bar">'
        '<div class="progress-fill" id="progress-fill" style="width:0%"></div>'
        '</div></div>'
    ) if target > 0 else ''

    return f'''\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>納品レビュー — {html.escape(client)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #0f0f0f; color: #e5e5e5; min-height: 100vh; }}

  /* ─── Header ─── */
  header {{
    background: #1a1a1a; border-bottom: 1px solid #2d2d2d;
    padding: 10px 16px; position: sticky; top: 0; z-index: 100;
  }}
  .hd-row1 {{
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 7px;
  }}
  .hd-title {{ font-size: 1rem; font-weight: 600; flex: 1; min-width: 140px; }}
  .hd-title .email {{ color: #6b7280; font-weight: 400; font-size: 0.8rem; margin-left: 8px; }}
  .hd-counter {{ text-align: center; }}
  .hd-count {{ font-size: 1.5rem; font-weight: 700; color: #22c55e; line-height: 1; }}
  .hd-count.over {{ color: #f59e0b; }}
  .hd-label {{ font-size: 0.7rem; color: #6b7280; margin-top: 2px; }}
  .progress-wrap {{ flex: 1; min-width: 80px; max-width: 160px; }}
  .progress-bar {{ height: 5px; background: #2d2d2d; border-radius: 3px; overflow: hidden; }}
  .progress-fill {{ height: 100%; background: #22c55e; border-radius: 3px; transition: width .3s; }}
  .progress-fill.over {{ background: #f59e0b; }}
  .export-btn {{
    background: #2563eb; color: #fff; border: none;
    padding: 6px 13px; border-radius: 5px; cursor: pointer;
    font-size: 0.8rem; white-space: nowrap; margin-left: auto;
  }}
  .export-btn:hover {{ background: #1d4ed8; }}

  /* ─── Stats bar ─── */
  .hd-stats {{
    display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.75rem;
    color: #6b7280; margin-bottom: 7px;
  }}
  .stat-item .n {{ font-weight: 600; }}
  .stat-sel  .n {{ color: #22c55e; }}
  .stat-r4   .n {{ color: #f59e0b; }}
  .stat-r3   .n {{ color: #d97706; }}
  .stat-r2   .n {{ color: #9ca3af; }}
  .stat-r1   .n {{ color: #6b7280; }}
  .stat-un   .n {{ color: #4b5563; }}

  /* ─── Controls row ─── */
  .hd-row2 {{
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }}
  .view-btn {{
    background: #2d2d2d; color: #9ca3af; border: 1px solid #3d3d3d;
    padding: 5px 11px; border-radius: 5px; cursor: pointer; font-size: 0.8rem;
  }}
  .view-btn.active {{ background: #374151; color: #e5e5e5; border-color: #4b5563; }}
  .view-btn:hover:not(.active) {{ background: #333; color: #ccc; }}
  select.sort-select {{
    background: #2d2d2d; color: #9ca3af; border: 1px solid #3d3d3d;
    padding: 5px 8px; border-radius: 5px; font-size: 0.8rem; cursor: pointer;
  }}
  select.sort-select:focus {{ outline: none; border-color: #4b5563; }}
  .bulk-btn {{
    background: #1e293b; color: #94a3b8; border: 1px solid #334155;
    padding: 5px 11px; border-radius: 5px; cursor: pointer; font-size: 0.8rem;
  }}
  .bulk-btn:hover {{ background: #273444; color: #e5e5e5; }}

  /* ─── Grid container ─── */
  #grid {{ padding: 12px; }}
  .grid-inner {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 6px;
  }}

  /* ─── Group view ─── */
  .group-section {{ margin-bottom: 18px; }}
  .group-header {{
    font-size: 0.72rem; color: #6b7280; padding: 5px 4px 4px;
    border-bottom: 1px solid #252525; margin-bottom: 6px;
    display: flex; align-items: center; gap: 10px;
  }}
  .group-header .g-dt {{ color: #4b5563; }}
  .group-header .g-cnt {{ color: #4b5563; }}

  /* ─── Card ─── */
  .card {{
    background: #1a1a1a; border-radius: 6px; overflow: hidden;
    border: 2px solid transparent; position: relative;
    transition: border-color .15s, opacity .15s;
  }}
  .card.selected {{ border-color: #22c55e; background: #0d2318; }}
  .card.neutral  {{ border-color: transparent; opacity: 1; }}
  .card.faded    {{ opacity: 0.45; }}

  .toggle-btn {{
    position: absolute; top: 5px; right: 5px; z-index: 10;
    width: 26px; height: 26px; border-radius: 50%; border: none;
    font-size: 13px; font-weight: 700; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .15s;
  }}
  .toggle-btn.off {{ background: rgba(0,0,0,.65); color: #9ca3af; }}
  .toggle-btn.on  {{ background: #22c55e; color: #fff; }}

  .thumb {{
    width: 100%; aspect-ratio: 3/2; object-fit: cover;
    display: block; cursor: zoom-in;
  }}

  /* ─── Card meta ─── */
  .card-meta {{ padding: 5px 7px 5px; }}
  .card-meta-row {{
    display: flex; justify-content: space-between;
    align-items: center; gap: 4px; margin-bottom: 4px;
  }}
  .filename {{ font-size: 0.7rem; color: #6b7280; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; flex: 1; }}
  .score {{ font-size: 0.7rem; color: #f59e0b; white-space: nowrap; }}

  /* ─── Star buttons ─── */
  .star-row {{ display: flex; gap: 2px; }}
  .star-btn {{
    background: none; border: none; cursor: pointer;
    font-size: 0.95rem; padding: 1px 2px; line-height: 1;
    color: #374151; transition: color .1s, transform .1s;
  }}
  .star-btn:hover {{ transform: scale(1.2); }}
  .star-btn.active {{ color: #f59e0b; }}

  /* ─── Lightbox ─── */
  #lightbox {{
    display: none; position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,.93);
    flex-direction: column; align-items: center; justify-content: center;
  }}
  #lightbox.open {{ display: flex; }}
  #lb-img {{ max-width: 90vw; max-height: 80vh; object-fit: contain; border-radius: 3px; }}
  #lb-bar {{
    display: flex; align-items: center; gap: 10px; margin-top: 10px;
    background: #1a1a1a; border-radius: 8px; padding: 8px 14px;
  }}
  .lb-nav {{
    background: none; border: none; color: #6b7280;
    font-size: 1.8rem; cursor: pointer; padding: 0 4px; line-height: 1;
    user-select: none;
  }}
  .lb-nav:hover:not(:disabled) {{ color: #fff; }}
  .lb-nav:disabled {{ opacity: .25; cursor: default; }}
  .lb-action {{
    border: none; padding: 6px 14px; border-radius: 6px;
    font-size: 0.85rem; font-weight: 600; cursor: pointer; transition: outline .1s;
  }}
  .lb-select {{ background: #166534; color: #fff; }}
  .lb-select:hover {{ background: #15803d; }}
  .lb-select.active {{ outline: 2px solid #4ade80; }}
  .lb-exclude {{ background: #1f1f1f; color: #9ca3af; }}
  .lb-exclude:hover {{ background: #2d2d2d; }}
  #lb-name {{ color: #6b7280; font-size: 0.8rem; min-width: 150px; text-align: center; }}
  #lb-close {{
    position: fixed; top: 14px; right: 18px; background: none; border: none;
    color: #6b7280; font-size: 26px; cursor: pointer; line-height: 1;
  }}
  #lb-close:hover {{ color: #fff; }}

  /* ─── Footer ─── */
  footer {{ text-align: center; padding: 28px 16px; color: #374151; font-size: 0.75rem; }}
</style>
</head>
<body>

<header>
  <div class="hd-row1">
    <div class="hd-title">
      {html.escape(client)}
      {'<span class="email">' + html.escape(email) + '</span>' if email else ''}
    </div>
    <div class="hd-counter">
      <div class="hd-count" id="sel-count">0</div>
      <div class="hd-label">選択中 / 目標 {target_str}</div>
    </div>
    {progress_html}
    <button class="export-btn" onclick="exportFeedback()">📤 フィードバック出力</button>
  </div>
  <div class="hd-stats" id="hd-stats">
    <span class="stat-item stat-sel">採用 <span class="n" id="stat-sel">0</span></span>
    <span class="stat-item stat-r4">★4 <span class="n" id="stat-r4">0</span></span>
    <span class="stat-item stat-r3">★3 <span class="n" id="stat-r3">0</span></span>
    <span class="stat-item stat-r2">★2 <span class="n" id="stat-r2">0</span></span>
    <span class="stat-item stat-r1">★1 <span class="n" id="stat-r1">0</span></span>
    <span class="stat-item stat-un">未 <span class="n" id="stat-un">{total}</span></span>
  </div>
  <div class="hd-row2">
    <button class="view-btn active" id="btn-grid"  onclick="setView('grid')">グリッド</button>
    <button class="view-btn"        id="btn-group" onclick="setView('group')">グループ</button>
    <select class="sort-select" id="sort-select" onchange="setSort(this.value)">
      <option value="default">デフォルト順</option>
      <option value="score">スコア降順</option>
      <option value="time">撮影時刻順</option>
      <option value="rating">レーティング降順</option>
    </select>
    <button class="bulk-btn" onclick="bulkSelectStar4()">★4を採用</button>
    <button class="bulk-btn" onclick="bulkClearAll()">選択解除</button>
  </div>
</header>

<div id="grid"></div>

<!-- ─── Lightbox ─── -->
<div id="lightbox">
  <button id="lb-close" onclick="closeLb()" title="閉じる (Esc)">×</button>
  <img id="lb-img" src="" alt="">
  <div id="lb-bar">
    <button class="lb-nav" id="lb-prev" onclick="lbMove(-1)" title="前へ (←)">&#8592;</button>
    <button class="lb-action lb-select" id="lb-select" onclick="lbToggleSelect()">✓ 選択中</button>
    <span id="lb-name"></span>
    <button class="lb-action lb-exclude" id="lb-exclude" onclick="lbExclude()">✗ 除外</button>
    <button class="lb-nav" id="lb-next" onclick="lbMove(1)" title="次へ (→)">&#8594;</button>
  </div>
</div>

<footer>
  全 {total} 枚&emsp;|&emsp;Aesthetic Shadowing Agent
</footer>

<script>
const STORAGE_KEY         = {json.dumps(storage_key)};
const STORAGE_KEY_RATINGS = {json.dumps(storage_key_ratings)};
const TARGET = {target};
const CLIENT = {json.dumps(client)};
const EMAIL  = {json.dumps(email)};
const PHOTOS = {photo_data_js};

let sel     = {{}};   // filename → true
let ratings = {{}};   // filename → 1..4
let currentView = 'grid';
let currentSort = 'default';
let lbPhotos    = [];   // 現在の表示順（ライトボックスナビ用）
let lbIdx       = -1;

// ─── State persistence ──────────────────────────────────────────────────────
function loadState() {{
  try {{ const s = localStorage.getItem(STORAGE_KEY); if (s) sel = JSON.parse(s); }} catch(e) {{}}
  try {{
    const r = localStorage.getItem(STORAGE_KEY_RATINGS);
    if (r) {{
      ratings = JSON.parse(r);
    }} else {{
      // initialRating (exiftool XMP:Rating) で初期化
      PHOTOS.forEach(p => {{ if (p.initialRating > 0) ratings[p.filename] = p.initialRating; }});
    }}
  }} catch(e) {{}}
}}
function saveSelState()     {{ localStorage.setItem(STORAGE_KEY,         JSON.stringify(sel));     }}
function saveRatingState()  {{ localStorage.setItem(STORAGE_KEY_RATINGS, JSON.stringify(ratings)); }}

// ─── Stats ──────────────────────────────────────────────────────────────────
function updateStats() {{
  const selN  = Object.values(sel).filter(v => v === true).length;
  const r4    = Object.values(ratings).filter(r => r === 4).length;
  const r3    = Object.values(ratings).filter(r => r === 3).length;
  const r2    = Object.values(ratings).filter(r => r === 2).length;
  const r1    = Object.values(ratings).filter(r => r === 1).length;
  const rated = r4 + r3 + r2 + r1;

  const selEl = document.getElementById('sel-count');
  selEl.textContent = selN;
  selEl.classList.toggle('over', TARGET > 0 && selN > TARGET);
  document.getElementById('stat-sel').textContent = selN;
  document.getElementById('stat-r4').textContent  = r4;
  document.getElementById('stat-r3').textContent  = r3;
  document.getElementById('stat-r2').textContent  = r2;
  document.getElementById('stat-r1').textContent  = r1;
  document.getElementById('stat-un').textContent  = PHOTOS.length - rated;

  if (TARGET > 0) {{
    const fill = document.getElementById('progress-fill');
    if (fill) {{
      fill.style.width = Math.min(100, (selN / TARGET) * 100) + '%';
      fill.classList.toggle('over', selN > TARGET);
    }}
  }}
}}

// ─── Sorting ─────────────────────────────────────────────────────────────────
function getSortedPhotos() {{
  const arr = PHOTOS.map(p => Object.assign({{}}, p));
  switch (currentSort) {{
    case 'score':
      arr.sort((a, b) => (b.compositeScore - a.compositeScore) || (b.starRating - a.starRating));
      break;
    case 'time':
      arr.sort((a, b) => (a.datetime || '').localeCompare(b.datetime || ''));
      break;
    case 'rating':
      arr.sort((a, b) => ((ratings[b.filename] || 0) - (ratings[a.filename] || 0))
                      || (b.compositeScore - a.compositeScore));
      break;
    // default: keep PHOTOS order (Python star_rating desc, score desc)
  }}
  return arr;
}}

// ─── datetime formatter ───────────────────────────────────────────────────────
function fmtDt(dt) {{
  if (!dt) return '';
  const m = dt.match(/^([0-9]{{4}}):([0-9]{{2}}):([0-9]{{2}}) ([0-9]{{2}}):([0-9]{{2}})/);
  return m ? (m[1] + '-' + m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5]) : dt.slice(0, 16);
}}

// ─── Card building ───────────────────────────────────────────────────────────
function buildCardEl(photo) {{
  const div = document.createElement('div');
  div.className = 'card neutral';
  div.dataset.filename = photo.filename;

  // Toggle btn
  const btn = document.createElement('button');
  btn.className = 'toggle-btn off';
  btn.title = '選択/除外を切り替える';
  btn.textContent = '✗';
  btn.addEventListener('click', e => {{ e.stopPropagation(); toggleCard(div); }});
  div.appendChild(btn);

  // Thumb
  const img = document.createElement('img');
  img.className = 'thumb';
  img.src = 'thumbs/' + photo.thumbName;
  img.alt = photo.filename;
  img.loading = 'lazy';
  img.addEventListener('click', () => openLb(photo.filename));
  div.appendChild(img);

  // Meta
  const meta = document.createElement('div');
  meta.className = 'card-meta';

  const row1 = document.createElement('div');
  row1.className = 'card-meta-row';
  const fname = document.createElement('span');
  fname.className = 'filename';
  fname.title = photo.filename;
  fname.textContent = photo.filename;
  const scoreEl = document.createElement('span');
  scoreEl.className = 'score';
  scoreEl.textContent = photo.compositeScore ? photo.compositeScore.toFixed(3) : '─';
  row1.appendChild(fname);
  row1.appendChild(scoreEl);
  meta.appendChild(row1);

  // Stars
  const starRow = document.createElement('div');
  starRow.className = 'star-row';
  for (let r = 1; r <= 4; r++) {{
    const sb = document.createElement('button');
    sb.className = 'star-btn';
    sb.textContent = '★';
    sb.dataset.r = r;
    sb.addEventListener('click', e => {{ e.stopPropagation(); setRating(photo.filename, r); }});
    starRow.appendChild(sb);
  }}
  meta.appendChild(starRow);
  div.appendChild(meta);

  applyCard(div);
  return div;
}}

// ─── Render ──────────────────────────────────────────────────────────────────
function renderGrid() {{
  const sorted  = getSortedPhotos();
  lbPhotos      = sorted;
  const container = document.getElementById('grid');
  container.innerHTML = '';
  const inner = document.createElement('div');
  inner.className = 'grid-inner';
  sorted.forEach(p => inner.appendChild(buildCardEl(p)));
  container.appendChild(inner);
}}

function renderGroups() {{
  const sorted = getSortedPhotos();
  // Group view: sort by groupId asc, then datetime asc within group
  const grouped = sorted.slice().sort((a, b) => {{
    if (a.groupId !== b.groupId) return a.groupId - b.groupId;
    return (a.datetime || '').localeCompare(b.datetime || '');
  }});
  lbPhotos = grouped;

  // Precompute group sizes
  const groupSizes = {{}};
  grouped.forEach(p => {{ groupSizes[p.groupId] = (groupSizes[p.groupId] || 0) + 1; }});

  const container = document.getElementById('grid');
  container.innerHTML = '';

  let curGroupId = null;
  let curInner   = null;

  grouped.forEach(p => {{
    if (p.groupId !== curGroupId) {{
      curGroupId = p.groupId;

      const section = document.createElement('div');
      section.className = 'group-section';

      const header = document.createElement('div');
      header.className = 'group-header';
      header.innerHTML = '<span>グループ ' + curGroupId + '</span>'
        + (p.datetime ? '<span class="g-dt">' + fmtDt(p.datetime) + '</span>' : '')
        + '<span class="g-cnt">' + groupSizes[curGroupId] + '枚</span>';
      section.appendChild(header);

      curInner = document.createElement('div');
      curInner.className = 'grid-inner';
      section.appendChild(curInner);
      container.appendChild(section);
    }}
    curInner.appendChild(buildCardEl(p));
  }});
}}

function render() {{
  if (currentView === 'grid') renderGrid(); else renderGroups();
  updateStats();
}}

// ─── View / Sort controls ─────────────────────────────────────────────────────
function setView(mode) {{
  currentView = mode;
  document.getElementById('btn-grid').classList.toggle('active',  mode === 'grid');
  document.getElementById('btn-group').classList.toggle('active', mode === 'group');
  render();
}}
function setSort(val) {{ currentSort = val; render(); }}

// ─── Card state ───────────────────────────────────────────────────────────────
function applyCard(card) {{
  const fn  = card.dataset.filename;
  const v   = sel[fn];
  const btn = card.querySelector('.toggle-btn');

  if (v === true) {{
    card.classList.add('selected');
    card.classList.remove('neutral', 'faded');
    btn.textContent = '✓';
    btn.classList.add('on');
    btn.classList.remove('off');
  }} else {{
    card.classList.remove('selected', 'faded');
    card.classList.add('neutral');
    btn.textContent = '✗';
    btn.classList.add('off');
    btn.classList.remove('on');
  }}

  // Sync star buttons
  const r = ratings[fn] || 0;
  card.querySelectorAll('.star-btn').forEach((sb, i) => {{
    sb.classList.toggle('active', i < r);
  }});
}}

function toggleCard(card) {{
  const fn = card.dataset.filename;
  if (sel[fn] === true) delete sel[fn]; else sel[fn] = true;
  applyCard(card);
  saveSelState();
  updateStats();
  lbRefreshButtons();
}}

// ─── Rating ──────────────────────────────────────────────────────────────────
function setRating(fn, r) {{
  // クリックで同じ値なら解除、違う値なら設定
  if (ratings[fn] === r) delete ratings[fn]; else ratings[fn] = r;
  document.querySelectorAll('.card[data-filename="' + CSS.escape(fn) + '"]').forEach(applyCard);
  saveRatingState();
  updateStats();
}}

// ─── Bulk operations ─────────────────────────────────────────────────────────
function bulkSelectStar4() {{
  PHOTOS.forEach(p => {{ if ((ratings[p.filename] || 0) === 4) sel[p.filename] = true; }});
  saveSelState();
  render();
}}
function bulkClearAll() {{
  sel = {{}};
  saveSelState();
  render();
}}

// ─── Lightbox ────────────────────────────────────────────────────────────────
function openLb(filename) {{
  const idx = lbPhotos.findIndex(p => p.filename === filename);
  if (idx < 0) return;
  lbIdx = idx;
  lbShow();
  document.getElementById('lightbox').classList.add('open');
}}
function closeLb() {{
  document.getElementById('lightbox').classList.remove('open');
  lbIdx = -1;
}}
function lbShow() {{
  if (lbIdx < 0 || lbIdx >= lbPhotos.length) return;
  const p = lbPhotos[lbIdx];
  document.getElementById('lb-img').src = 'preview/' + p.thumbName;
  document.getElementById('lb-name').textContent = p.filename;
  document.getElementById('lb-prev').disabled = lbIdx === 0;
  document.getElementById('lb-next').disabled  = lbIdx === lbPhotos.length - 1;
  lbRefreshButtons();
}}
function lbRefreshButtons() {{
  if (lbIdx < 0 || lbIdx >= lbPhotos.length) return;
  document.getElementById('lb-select').classList.toggle('active',
    sel[lbPhotos[lbIdx].filename] === true);
}}
function lbMove(d) {{
  const next = lbIdx + d;
  if (next < 0 || next >= lbPhotos.length) return;
  lbIdx = next;
  lbShow();
}}
function lbToggleSelect() {{
  if (lbIdx < 0) return;
  const fn = lbPhotos[lbIdx].filename;
  if (sel[fn] === true) delete sel[fn]; else sel[fn] = true;
  document.querySelectorAll('.card[data-filename="' + CSS.escape(fn) + '"]').forEach(applyCard);
  saveSelState();
  updateStats();
  lbRefreshButtons();
}}
function lbExclude() {{
  if (lbIdx < 0) return;
  const fn = lbPhotos[lbIdx].filename;
  delete sel[fn];
  document.querySelectorAll('.card[data-filename="' + CSS.escape(fn) + '"]').forEach(applyCard);
  saveSelState();
  updateStats();
  lbRefreshButtons();
}}

// ─── Keyboard ────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {{
  const lb = document.getElementById('lightbox');
  if (!lb.classList.contains('open')) return;
  if (e.key === 'ArrowLeft')                  {{ lbMove(-1); return; }}
  if (e.key === 'ArrowRight')                 {{ lbMove(1);  return; }}
  if (e.key === 'Escape')                     {{ closeLb();  return; }}
  if (e.key === ' ' || e.key === 'Enter') {{ e.preventDefault(); lbToggleSelect(); }}
}});
document.getElementById('lightbox').addEventListener('click', e => {{
  if (e.target === document.getElementById('lightbox')) closeLb();
}});

// ─── Export ──────────────────────────────────────────────────────────────────
function exportFeedback() {{
  const selected = PHOTOS.map(p => p.filename).filter(fn => sel[fn] === true);
  const ratingsExport = {{}};
  PHOTOS.forEach(p => {{ if (ratings[p.filename]) ratingsExport[p.filename] = ratings[p.filename]; }});
  const data = {{
    client:         CLIENT,
    email:          EMAIL,
    target:         TARGET,
    total_photos:   PHOTOS.length,
    selected_count: selected.length,
    selected:       selected,
    ratings:        ratingsExport,
    exported_at:    new Date().toISOString(),
  }};
  const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'feedback_' + new Date().toISOString().slice(0, 10) + '.json';
  a.click();
  URL.revokeObjectURL(url);
}}

// ─── Init ────────────────────────────────────────────────────────────────────
loadState();
render();
</script>

</body>
</html>
'''


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    jpeg_dir = Path(args.jpeg_dir).expanduser().resolve()
    out_dir  = Path(args.output).expanduser()

    if not jpeg_dir.is_dir():
        print(f"ERROR: JPEGフォルダが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    jpegs = find_jpegs(jpeg_dir)
    if not jpegs:
        print(f"ERROR: JPEGが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    scores = load_scores(args.csv)
    photos = build_photo_list(jpegs, scores)

    print(f"  対象: {len(photos)} 枚", file=sys.stderr)

    generate_images(jpegs, jpeg_dir, out_dir)

    storage_key         = f"delivery_{out_dir.name}_selections"
    storage_key_ratings = f"asa-delivery-ratings-{out_dir.name}"

    html_content = generate_html(
        photos,
        client=args.client,
        email=args.email,
        target=args.target,
        storage_key=storage_key,
        storage_key_ratings=storage_key_ratings,
        show_ai_rating=not args.no_ai_rating,
    )

    index_path = out_dir / 'index.html'
    index_path.write_text(html_content, encoding='utf-8')

    print(f"  生成完了: {out_dir}/", file=sys.stderr)
    print(f"  index.html: {index_path}", file=sys.stderr)
    if args.target > 0:
        print(f"  目標枚数: {args.target} 枚", file=sys.stderr)
    print(f"\nNetlify Drop で共有: https://app.netlify.com/drop", file=sys.stderr)


if __name__ == '__main__':
    main()
