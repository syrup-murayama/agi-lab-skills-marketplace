#!/usr/bin/env python3
"""
Delivery Review ツール

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
import sys
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
    return p.parse_args()


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
    """star_rating降順 → composite_score降順でソートした写真リストを返す"""
    photos = []
    for src in jpegs:
        name = src.name
        sc = scores.get(name, {})
        photos.append({
            'filename': name,
            'thumb_name': src.stem + '.jpg',
            'star_rating': sc.get('star_rating', 0),
            'composite_score': sc.get('composite_score', 0.0),
        })
    photos.sort(key=lambda x: (-x['star_rating'], -x['composite_score']))
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
    thumbs_dir = out_dir / 'thumbs'
    preview_dir = out_dir / 'preview'
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    total = len(jpegs)
    for i, src in enumerate(jpegs, 1):
        stem = src.stem
        make_resized(src, thumbs_dir / (stem + '.jpg'), 300, 70)
        make_resized(src, preview_dir / (stem + '.jpg'), 1500, 85)
        if i % 20 == 0 or i == total:
            print(f"\r  画像生成中: {i}/{total}", end='', file=sys.stderr, flush=True)
    print(f"\r  完了: thumbs/preview を {total} 枚生成            ", file=sys.stderr)


# ─── HTML生成 ─────────────────────────────────────────────────────────────────

def star_label(r: int) -> str:
    return '★' * r if r > 0 else '─'


def build_cards_html(photos: list[dict]) -> str:
    parts = []
    for idx, p in enumerate(photos):
        fn = html.escape(p['filename'])
        tn = html.escape(p['thumb_name'])
        star = star_label(p['star_rating'])
        score_txt = f"{p['composite_score']:.3f}" if p['composite_score'] else '─'
        parts.append(f'''\
      <div class="card" data-filename="{fn}" data-index="{idx}">
        <button class="toggle-btn" onclick="toggleCard(this)" title="選択/除外を切り替える">✗</button>
        <img class="thumb" src="thumbs/{tn}" alt="{fn}" loading="lazy">
        <div class="card-meta">
          <span class="filename">{fn}</span>
          <span class="score">{star} {score_txt}</span>
        </div>
      </div>''')
    return '\n'.join(parts)


def generate_html(photos: list[dict], client: str, email: str, target: int,
                  storage_key: str) -> str:
    cards_html = build_cards_html(photos)
    target_str = str(target) if target > 0 else '─'
    total = len(photos)

    photo_data_js = json.dumps(
        [{'filename': p['filename'], 'thumbName': p['thumb_name']} for p in photos],
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
    padding: 12px 20px; position: sticky; top: 0; z-index: 100;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  }}
  .hd-title {{ font-size: 1rem; font-weight: 600; flex: 1; min-width: 160px; }}
  .hd-title .email {{ color: #6b7280; font-weight: 400; font-size: 0.8rem; margin-left: 8px; }}
  .hd-counter {{ text-align: center; }}
  .hd-count {{ font-size: 1.5rem; font-weight: 700; color: #22c55e; line-height: 1; }}
  .hd-count.over {{ color: #f59e0b; }}
  .hd-label {{ font-size: 0.7rem; color: #6b7280; margin-top: 2px; }}
  .progress-wrap {{ flex: 1; min-width: 100px; max-width: 180px; }}
  .progress-bar {{ height: 5px; background: #2d2d2d; border-radius: 3px; overflow: hidden; }}
  .progress-fill {{ height: 100%; background: #22c55e; border-radius: 3px; transition: width .3s; }}
  .progress-fill.over {{ background: #f59e0b; }}
  .export-btn {{
    background: #2563eb; color: #fff; border: none;
    padding: 7px 14px; border-radius: 6px; cursor: pointer;
    font-size: 0.85rem; white-space: nowrap;
  }}
  .export-btn:hover {{ background: #1d4ed8; }}

  /* ─── Grid ─── */
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 6px; padding: 12px;
  }}

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

  .card-meta {{
    padding: 5px 7px; display: flex; justify-content: space-between;
    align-items: center; gap: 4px;
  }}
  .filename {{ font-size: 0.7rem; color: #6b7280; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; flex: 1; }}
  .score {{ font-size: 0.7rem; color: #f59e0b; white-space: nowrap; }}

  /* ─── Lightbox ─── */
  #lightbox {{
    display: none; position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,.93);
    flex-direction: column; align-items: center; justify-content: center;
  }}
  #lightbox.open {{ display: flex; }}
  #lb-img {{
    max-width: 90vw; max-height: 80vh;
    object-fit: contain; border-radius: 3px;
  }}
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
    font-size: 0.85rem; font-weight: 600; cursor: pointer;
    transition: outline .1s;
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
  footer {{
    text-align: center; padding: 28px 16px;
    color: #374151; font-size: 0.75rem;
  }}
</style>
</head>
<body>

<header>
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
</header>

<div class="grid" id="grid">
{cards_html}
</div>

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
const STORAGE_KEY = {json.dumps(storage_key)};
const TARGET      = {target};
const CLIENT      = {json.dumps(client)};
const EMAIL       = {json.dumps(email)};
const PHOTOS      = {photo_data_js};

// ─── State: filename → true (selected) / undefined (neutral) ─────────────
let sel = {{}};

function loadState() {{
  try {{ const s = localStorage.getItem(STORAGE_KEY); if (s) sel = JSON.parse(s); }}
  catch(e) {{}}
}}
function saveState() {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sel));
  updateCounter();
}}

// ─── Counter / progress bar ──────────────────────────────────────────────
function updateCounter() {{
  const n = Object.values(sel).filter(v => v === true).length;
  const el = document.getElementById('sel-count');
  el.textContent = n;
  el.classList.toggle('over', TARGET > 0 && n > TARGET);
  if (TARGET > 0) {{
    const fill = document.getElementById('progress-fill');
    if (fill) {{
      const pct = Math.min(100, (n / TARGET) * 100);
      fill.style.width = pct + '%';
      fill.classList.toggle('over', n > TARGET);
    }}
  }}
}}

// ─── Card state rendering ─────────────────────────────────────────────────
function applyCard(card) {{
  const fn = card.dataset.filename;
  const v  = sel[fn];
  const btn = card.querySelector('.toggle-btn');
  if (v === true) {{
    card.classList.replace('neutral', 'selected') || card.classList.add('selected');
    card.classList.remove('faded', 'neutral');
    btn.textContent = '✓';
    btn.classList.replace('off', 'on') || btn.classList.add('on');
    btn.classList.remove('off');
  }} else {{
    card.classList.remove('selected');
    card.classList.add('neutral');
    card.classList.remove('faded');
    btn.textContent = '✗';
    btn.classList.replace('on', 'off') || btn.classList.add('off');
    btn.classList.remove('on');
  }}
}}

function toggleCard(btn) {{
  const card = btn.closest('.card');
  const fn   = card.dataset.filename;
  sel[fn] = sel[fn] === true ? undefined : true;
  if (sel[fn] === undefined) delete sel[fn];
  applyCard(card);
  saveState();
  if (lbIdx >= 0 && PHOTOS[lbIdx].filename === fn) lbRefreshButtons();
}}

// ─── Lightbox ────────────────────────────────────────────────────────────
let lbIdx = -1;

function openLb(idx) {{
  if (idx < 0 || idx >= PHOTOS.length) return;
  lbIdx = idx;
  lbShow();
  document.getElementById('lightbox').classList.add('open');
}}
function closeLb() {{
  document.getElementById('lightbox').classList.remove('open');
  lbIdx = -1;
}}
function lbShow() {{
  const p = PHOTOS[lbIdx];
  document.getElementById('lb-img').src = 'preview/' + p.thumbName;
  document.getElementById('lb-name').textContent = p.filename;
  document.getElementById('lb-prev').disabled = lbIdx === 0;
  document.getElementById('lb-next').disabled = lbIdx === PHOTOS.length - 1;
  lbRefreshButtons();
}}
function lbRefreshButtons() {{
  if (lbIdx < 0) return;
  const v  = sel[PHOTOS[lbIdx].filename];
  const sb = document.getElementById('lb-select');
  sb.classList.toggle('active', v === true);
}}
function lbMove(d) {{
  const next = lbIdx + d;
  if (next < 0 || next >= PHOTOS.length) return;
  lbIdx = next;
  lbShow();
}}
function lbToggleSelect() {{
  if (lbIdx < 0) return;
  const fn = PHOTOS[lbIdx].filename;
  sel[fn] = sel[fn] === true ? undefined : true;
  if (sel[fn] === undefined) delete sel[fn];
  const card = document.querySelector('.card[data-index="' + lbIdx + '"]');
  if (card) applyCard(card);
  saveState();
  lbRefreshButtons();
}}
function lbExclude() {{
  // 除外 = 選択を外す（neutral に戻す）
  if (lbIdx < 0) return;
  const fn = PHOTOS[lbIdx].filename;
  delete sel[fn];
  const card = document.querySelector('.card[data-index="' + lbIdx + '"]');
  if (card) applyCard(card);
  saveState();
  lbRefreshButtons();
}}

// ─── Keyboard ────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {{
  const lb = document.getElementById('lightbox');
  if (!lb.classList.contains('open')) return;
  if (e.key === 'ArrowLeft')  {{ lbMove(-1); return; }}
  if (e.key === 'ArrowRight') {{ lbMove(1);  return; }}
  if (e.key === 'Escape')     {{ closeLb();  return; }}
  if (e.key === ' ' || e.key === 'Enter') {{ e.preventDefault(); lbToggleSelect(); }}
}});

// Lightbox backdrop click
document.getElementById('lightbox').addEventListener('click', e => {{
  if (e.target === document.getElementById('lightbox')) closeLb();
}});

// ─── Thumb click → lightbox ───────────────────────────────────────────────
document.querySelectorAll('.thumb').forEach(img => {{
  img.addEventListener('click', () => {{
    const idx = parseInt(img.closest('.card').dataset.index, 10);
    openLb(idx);
  }});
}});

// ─── Export ──────────────────────────────────────────────────────────────
function exportFeedback() {{
  const selected = PHOTOS.map(p => p.filename).filter(fn => sel[fn] === true);
  const data = {{
    client:         CLIENT,
    email:          EMAIL,
    target:         TARGET,
    total_photos:   PHOTOS.length,
    selected_count: selected.length,
    selected:       selected,
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

// ─── Init ────────────────────────────────────────────────────────────────
loadState();
document.querySelectorAll('.card').forEach(applyCard);
updateCounter();
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

    # 写真リスト構築
    jpegs = find_jpegs(jpeg_dir)
    if not jpegs:
        print(f"ERROR: JPEGが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    scores = load_scores(args.csv)
    photos = build_photo_list(jpegs, scores)

    print(f"  対象: {len(photos)} 枚", file=sys.stderr)

    # thumbs / preview 生成
    generate_images(jpegs, jpeg_dir, out_dir)

    # HTML 生成
    storage_key = f"delivery_{out_dir.name}_selections"
    html_content = generate_html(
        photos,
        client=args.client,
        email=args.email,
        target=args.target,
        storage_key=storage_key,
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
