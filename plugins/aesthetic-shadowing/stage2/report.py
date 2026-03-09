#!/usr/bin/env python3
"""
Stage 2 HTMLレポート生成ツール

stage2_groups.csv を読み込み、シーングループ単位で
サムネイルを並べた閲覧用 HTML を生成する。

使い方:
  python report.py <jpeg_dir> <stage2_csv> [--output stage2_report.html]

例:
  python report.py \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/S2_JPEG/ \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/stage2_groups.csv
"""

import argparse
import csv
import html
import sys
from itertools import groupby
from pathlib import Path


# ---------- データ読み込み ----------

def load_groups(csv_path: Path) -> list[dict]:
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append({
                'stem':         Path(row['file']).stem,
                'file':         row['file'],
                'datetime':     row['datetime'],
                'group_id':     int(row['group_id']),
                'group_size':   int(row['group_size']),
                'position':     row['position'],
                'bonus_weight': float(row['bonus_weight']),
                'person_count': int(float(row['person_count'])),
            })
    return rows


def find_jpeg(jpeg_dir: Path, stem: str) -> str:
    for ext in ('.JPG', '.jpg', '.jpeg', '.JPEG'):
        p = jpeg_dir / (stem + ext)
        if p.exists():
            return p.as_uri()
    return ''


# ---------- カード生成 ----------

POSITION_STYLE = {
    'first':  ('#6366f1', 'FIRST'),
    'last':   ('#06b6d4', 'LAST'),
    'middle': ('#475569', 'MID'),
    'solo':   ('#f59e0b', 'SOLO'),
}

BONUS_BAR_COLOR = {
    'first':  '#6366f1',
    'last':   '#06b6d4',
    'middle': '#334155',
    'solo':   '#f59e0b',
}


def make_card(row: dict, jpeg_url: str) -> str:
    pos = row['position']
    badge_color, badge_label = POSITION_STYLE.get(pos, ('#475569', pos.upper()))
    bar_color = BONUS_BAR_COLOR.get(pos, '#475569')
    bonus = row['bonus_weight']
    bonus_pct = min((bonus - 1.0) / 0.5 * 100, 100)  # 1.0〜1.5 → 0〜100%

    stem = html.escape(row['stem'])
    dt = row['datetime'][11:19] if len(row['datetime']) >= 19 else ''
    n_persons = row['person_count']
    person_icon = '👤' if n_persons == 1 else ('👥' if n_persons >= 2 else '─')
    person_label = f'{n_persons}人' if n_persons > 0 else '0人'

    return f'''
<div class="card pos-{pos}" onclick="openModal('{jpeg_url}','{stem}')">
  <div class="thumb-wrap">
    <img src="{jpeg_url}" alt="{stem}" loading="lazy">
    <span class="pos-badge" style="background:{badge_color}">{badge_label}</span>
  </div>
  <div class="card-info">
    <div class="card-name">{stem}</div>
    <div class="card-meta">
      <span class="card-time">{dt}</span>
      <span class="card-person">{person_icon} {person_label}</span>
    </div>
    <div class="bonus-row">
      <span class="bonus-label">bonus</span>
      <div class="bonus-bg">
        <div class="bonus-bar" style="width:{bonus_pct:.0f}%;background:{bar_color}"></div>
      </div>
      <span class="bonus-val">×{bonus:.1f}</span>
    </div>
  </div>
</div>'''


def make_group_section(gid: int, members: list[dict], jpeg_dir: Path) -> str:
    size = members[0]['group_size']
    dt_start = members[0]['datetime'][11:19] if members[0]['datetime'] else '?'
    n_persons_avg = sum(m['person_count'] for m in members) / len(members)

    cards = '\n'.join(
        make_card(m, find_jpeg(jpeg_dir, m['stem']))
        for m in members
    )

    person_summary = f'{n_persons_avg:.1f}人/枚'

    return f'''
<section class="group" id="g{gid}">
  <div class="group-header">
    <span class="gid">Group {gid}</span>
    <span class="gsize">{size}枚</span>
    <span class="gtime">{dt_start}</span>
    <span class="gpersons">👤 {person_summary}</span>
  </div>
  <div class="group-cards">
{cards}
  </div>
</section>'''


# ---------- HTML生成 ----------

def generate_html(rows: list[dict], jpeg_dir: Path) -> str:
    # グループ単位に整理
    sorted_rows = sorted(rows, key=lambda r: (r['group_id'], r['datetime'], r['stem']))
    groups_html_parts = []
    for gid, it in groupby(sorted_rows, key=lambda r: r['group_id']):
        members = list(it)
        groups_html_parts.append(make_group_section(gid, members, jpeg_dir))

    groups_html = '\n'.join(groups_html_parts)

    n_groups = max(r['group_id'] for r in rows) + 1
    n_shots  = len(rows)
    n_solo   = sum(1 for r in rows if r['position'] == 'solo')
    n_first  = sum(1 for r in rows if r['position'] == 'first')
    n_last   = sum(1 for r in rows if r['position'] == 'last')
    n_middle = sum(1 for r in rows if r['position'] == 'middle')

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 グループレポート</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0a0f1e;
    color: #e2e8f0;
    min-height: 100vh;
  }}

  /* ヘッダー */
  .header {{
    background: #0f1829;
    border-bottom: 1px solid #1e2d45;
    padding: 18px 24px;
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header h1 {{ font-size: 1.1rem; color: #f1f5f9; margin-bottom: 14px; }}
  .header h1 span {{ color: #818cf8; }}

  .stats {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }}
  .stat {{
    background: #0a0f1e;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    padding: 6px 14px;
    text-align: center;
    min-width: 80px;
  }}
  .stat .n {{ font-size: 1.3rem; font-weight: 700; }}
  .stat .l {{ font-size: 0.68rem; color: #64748b; margin-top: 2px; }}
  .s-group  .n {{ color: #818cf8; }}
  .s-solo   .n {{ color: #f59e0b; }}
  .s-first  .n {{ color: #6366f1; }}
  .s-last   .n {{ color: #06b6d4; }}
  .s-middle .n {{ color: #475569; }}

  /* フィルター */
  .filters {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }}
  .filter-btn {{
    padding: 4px 12px;
    border-radius: 16px;
    border: 1px solid #1e2d45;
    background: transparent;
    color: #94a3b8;
    cursor: pointer;
    font-size: 0.78rem;
    transition: all 0.15s;
  }}
  .filter-btn:hover  {{ border-color: #818cf8; color: #818cf8; }}
  .filter-btn.active {{ background: #818cf8; border-color: #818cf8; color: #fff; }}
  .filter-btn.f-solo.active   {{ background: #f59e0b; border-color: #f59e0b; }}
  .filter-btn.f-first.active  {{ background: #6366f1; border-color: #6366f1; }}
  .filter-btn.f-last.active   {{ background: #06b6d4; border-color: #06b6d4; }}
  .filter-btn.f-multi.active  {{ background: #22c55e; border-color: #22c55e; }}

  .jump-input {{
    padding: 4px 10px;
    border-radius: 16px;
    border: 1px solid #1e2d45;
    background: #0a0f1e;
    color: #94a3b8;
    font-size: 0.78rem;
    width: 110px;
  }}
  .jump-input::placeholder {{ color: #475569; }}

  /* コンテンツ */
  .content {{ padding: 20px 24px; }}
  .counter {{ color: #475569; font-size: 0.78rem; margin-bottom: 16px; }}

  /* グループセクション */
  .group {{
    margin-bottom: 28px;
    border: 1px solid #1e2d45;
    border-radius: 12px;
    overflow: hidden;
  }}

  .group-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    background: #0f1829;
    border-bottom: 1px solid #1e2d45;
    font-size: 0.8rem;
  }}
  .gid    {{ font-weight: 700; color: #818cf8; min-width: 70px; }}
  .gsize  {{ color: #94a3b8; }}
  .gtime  {{ color: #64748b; }}
  .gpersons {{ color: #64748b; margin-left: auto; }}

  .group-cards {{
    display: flex;
    flex-wrap: wrap;
    gap: 0;
    padding: 12px;
    gap: 10px;
  }}

  /* カード */
  .card {{
    width: 180px;
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    overflow: hidden;
    cursor: zoom-in;
    transition: transform 0.15s, border-color 0.15s;
    flex-shrink: 0;
  }}
  .card:hover {{ transform: translateY(-2px); border-color: #818cf8; }}
  .card.pos-first  {{ border-color: #4338ca; }}
  .card.pos-last   {{ border-color: #0891b2; }}
  .card.pos-solo   {{ border-color: #d97706; }}

  .thumb-wrap {{
    position: relative;
    aspect-ratio: 3/2;
    background: #0a0f1e;
    overflow: hidden;
  }}
  .thumb-wrap img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform 0.2s;
  }}
  .card:hover .thumb-wrap img {{ transform: scale(1.04); }}

  .pos-badge {{
    position: absolute;
    top: 6px;
    right: 6px;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.62rem;
    font-weight: 800;
    color: #fff;
    letter-spacing: 0.05em;
  }}

  .card-info {{ padding: 8px 10px; }}
  .card-name {{
    font-size: 0.68rem;
    color: #64748b;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-bottom: 4px;
  }}
  .card-meta {{
    display: flex;
    justify-content: space-between;
    font-size: 0.68rem;
    color: #475569;
    margin-bottom: 6px;
  }}
  .card-person {{ color: #94a3b8; }}

  .bonus-row {{
    display: grid;
    grid-template-columns: 36px 1fr 28px;
    align-items: center;
    gap: 5px;
  }}
  .bonus-label {{ font-size: 0.62rem; color: #475569; }}
  .bonus-bg {{
    background: #0a0f1e;
    border-radius: 3px;
    height: 4px;
    overflow: hidden;
  }}
  .bonus-bar {{ height: 100%; border-radius: 3px; }}
  .bonus-val {{ font-size: 0.62rem; color: #94a3b8; text-align: right; }}

  /* モーダル */
  .modal {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.88);
    z-index: 1000;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 10px;
  }}
  .modal.open {{ display: flex; }}
  .modal img {{
    max-width: 92vw;
    max-height: 86vh;
    border-radius: 6px;
    box-shadow: 0 8px 40px rgba(0,0,0,0.7);
  }}
  .modal-name {{ color: #94a3b8; font-size: 0.8rem; }}
  .modal-close {{
    position: absolute;
    top: 14px; right: 18px;
    background: none;
    border: none;
    color: #64748b;
    font-size: 2rem;
    cursor: pointer;
    line-height: 1;
  }}
  .modal-close:hover {{ color: #f1f5f9; }}

  .hidden {{ display: none !important; }}
</style>
</head>
<body>

<div class="header">
  <h1>Aesthetic Shadowing Agent — <span>Stage 2 グループレポート</span></h1>

  <div class="stats">
    <div class="stat s-group">
      <div class="n">{n_groups}</div><div class="l">グループ</div>
    </div>
    <div class="stat">
      <div class="n">{n_shots}</div><div class="l">総ショット</div>
    </div>
    <div class="stat s-solo">
      <div class="n">{n_solo}</div><div class="l">SOLO</div>
    </div>
    <div class="stat s-first">
      <div class="n">{n_first}</div><div class="l">FIRST</div>
    </div>
    <div class="stat s-last">
      <div class="n">{n_last}</div><div class="l">LAST</div>
    </div>
    <div class="stat s-middle">
      <div class="n">{n_middle}</div><div class="l">MIDDLE</div>
    </div>
  </div>

  <div class="filters">
    <button class="filter-btn active" onclick="setFilter('all',this)">全グループ</button>
    <button class="filter-btn f-multi" onclick="setFilter('multi',this)">複数枚のみ</button>
    <button class="filter-btn f-solo"  onclick="setFilter('solo',this)">SOLO のみ</button>
    <input  class="jump-input" id="jump-input"
            placeholder="Group番号へ…"
            onkeydown="if(event.key==='Enter')jumpTo(this.value)">
  </div>
</div>

<div class="content">
  <div class="counter" id="counter">表示: {n_groups} グループ</div>
  <div id="groups">
{groups_html}
  </div>
</div>

<!-- モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img id="modal-img" src="" alt="">
  <div class="modal-name" id="modal-name"></div>
</div>

<script>
function setFilter(type, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  let visible = 0;
  document.querySelectorAll('.group').forEach(g => {{
    const size = parseInt(g.querySelector('.gsize').textContent);
    let show = false;
    if (type === 'all')   show = true;
    if (type === 'multi') show = size > 1;
    if (type === 'solo')  show = size === 1;
    g.classList.toggle('hidden', !show);
    if (show) visible++;
  }});
  document.getElementById('counter').textContent = '表示: ' + visible + ' グループ';
}}

function jumpTo(val) {{
  const id = 'g' + parseInt(val);
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  document.getElementById('jump-input').value = '';
}}

function openModal(url, name) {{
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = name;
  document.getElementById('modal').classList.add('open');
}}
function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal')) {{
    document.getElementById('modal').classList.remove('open');
    document.getElementById('modal-img').src = '';
  }}
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body>
</html>'''


# ---------- メイン ----------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage 2 HTMLグループレポート生成',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir',   help='S2 JPEGフォルダ')
    parser.add_argument('stage2_csv', help='stage2_groups.csv')
    parser.add_argument('--output',   default='stage2_report.html')
    args = parser.parse_args()

    jpeg_dir  = Path(args.jpeg_dir)
    csv_path  = Path(args.stage2_csv)

    for p, name in [(jpeg_dir, 'JPEGフォルダ'), (csv_path, 'CSV')]:
        if not p.exists():
            print(f'エラー: {name} が見つかりません: {p}')
            sys.exit(1)

    rows = load_groups(csv_path)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = csv_path.parent / output_path

    html_str = generate_html(rows, jpeg_dir)
    output_path.write_text(html_str, encoding='utf-8')

    n_groups = max(r['group_id'] for r in rows) + 1
    print(f'レポート生成完了: {output_path}')
    print(f'グループ数: {n_groups} / ショット数: {len(rows)}')
    print(f'\n  open "{output_path}"')


if __name__ == '__main__':
    main()
