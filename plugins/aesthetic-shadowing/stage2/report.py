#!/usr/bin/env python3
"""
Stage 2 HTMLレポート生成ツール

stage2_groups.csv を読み込み、シーングループ単位で
サムネイルを並べた閲覧用 HTML を生成する。

使い方:
  python report.py <jpeg_dir> <stage2_csv> [--output stage2_report.html]
                   [--session-json session.json]

例:
  python report.py \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/S2_JPEG/ \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/stage2_groups.csv
"""

import argparse
import csv
import html
import json
import sys
from itertools import groupby
from pathlib import Path


# ---------- データ読み込み ----------

def load_groups(csv_path: Path) -> list[dict]:
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append({
                'stem':            Path(row['file']).stem,
                'file':            row['file'],
                'datetime':        row['datetime'],
                'group_id':        int(row['group_id']),
                'group_size':      int(row['group_size']),
                'position':        row['position'],
                'bonus_weight':    float(row['bonus_weight']),
                'person_count':    int(float(row['person_count'])),
                'sharpness_score': float(row.get('sharpness_score', 0.0)),
                'exposure_score':  float(row.get('exposure_score', 0.0)),
                'technical_score': float(row.get('technical_score', 0.0)),
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


def tech_score_color(score: float) -> str:
    if score >= 0.8:
        return '#22c55e'   # 緑
    elif score >= 0.5:
        return '#f59e0b'   # 黄
    else:
        return '#ef4444'   # 赤


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

    tech  = row['technical_score']
    sharp = row['sharpness_score']
    expo  = row['exposure_score']
    tech_color = tech_score_color(tech)
    gid = row['group_id']

    return f'''
<div class="card pos-{pos}" data-stem="{stem}" data-gid="{gid}" data-url="{jpeg_url}"
  data-sharpness="{sharp:.3f}"
  data-exposure="{expo:.3f}"
  data-persons="{n_persons}"
  data-position="{pos}"
  onclick="openModal('{jpeg_url}','{stem}',{gid})">
  <div class="thumb-wrap">
    <img src="{jpeg_url}" alt="{stem}" loading="lazy">
    <span class="pos-badge" style="background:{badge_color}">{badge_label}</span>
    <div class="select-overlay" id="sol-{stem}"></div>
    <button class="select-badge" id="sbadge-{stem}" onclick="toggleSelect(event,'{stem}')" title="セレクト切り替え"></button>
  </div>
  <div class="card-info">
    <div class="card-name">{stem}</div>
    <div class="card-meta">
      <span class="card-time">{dt}</span>
      <span class="card-person">{person_icon} {person_label}</span>
    </div>
    <div class="tech-row">
      <span class="tech-main" style="color:{tech_color}">技術 {tech:.2f}</span>
      <span class="tech-sub">鮮 {sharp:.2f} / 露 {expo:.2f}</span>
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
    max_tech = max(m['technical_score'] for m in members)
    max_tech_color = tech_score_color(max_tech)

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
    <span class="gtech" style="color:{max_tech_color}">最高技術 {max_tech:.2f}</span>
    <span class="gpersons">👤 {person_summary}</span>
  </div>
  <div class="group-cards">
{cards}
  </div>
</section>'''


# ---------- HTML生成 ----------

def make_session_panel(session_info: dict) -> str:
    title   = html.escape(session_info.get('title', ''))
    date    = html.escape(session_info.get('date', ''))
    purpose = html.escape(session_info.get('purpose', ''))
    note    = html.escape(session_info.get('session_note', ''))
    return f'''<div class="session-panel">
  <div class="session-header">
    <span class="session-title">📷 {title}</span>
    <span class="session-date">{date}</span>
  </div>
  <div class="session-purpose">目的: {purpose}</div>
  <div class="session-memo-label">メモ:</div>
  <textarea id="session-note" class="session-note" placeholder="メモを入力…">{note}</textarea>
</div>'''


def generate_html(rows: list[dict], jpeg_dir: Path, session_info: dict | None = None) -> str:
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

    tech_scores = [r['technical_score'] for r in rows]
    tech_avg  = sum(tech_scores) / len(tech_scores) if tech_scores else 0.0
    top10_threshold = sorted(tech_scores, reverse=True)[max(0, len(tech_scores) // 10 - 1)]
    n_top10  = sum(1 for s in tech_scores if s >= top10_threshold)
    tech_avg_color = tech_score_color(tech_avg)

    session_panel_html = make_session_panel(session_info) if session_info else ''
    # JS用: session_noteの初期値（JSON文字列としてエスケープ）
    session_note_init = json.dumps(session_info.get('session_note', '') if session_info else '')

    # JS用: 全ショットリスト（グループ順）
    all_shots_json = json.dumps(
        [{'url': find_jpeg(jpeg_dir, r['stem']), 'stem': r['stem'], 'gid': r['group_id']}
         for r in sorted_rows],
        ensure_ascii=False,
    )

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
  .s-tech   .n {{ color: #22c55e; }}
  .s-top10  .n {{ color: #22c55e; }}

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
  .gtech  {{ font-size: 0.75rem; font-weight: 600; }}
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
  .tech-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 5px;
  }}
  .tech-main {{ font-size: 0.78rem; font-weight: 700; }}
  .tech-sub  {{ font-size: 0.6rem; color: #475569; }}

  .bonus-label {{ font-size: 0.62rem; color: #475569; }}
  .bonus-bg {{
    background: #0a0f1e;
    border-radius: 3px;
    height: 4px;
    overflow: hidden;
  }}
  .bonus-bar {{ height: 100%; border-radius: 3px; }}
  .bonus-val {{ font-size: 0.62rem; color: #94a3b8; text-align: right; }}

  /* セレクトオーバーレイ・バッジ */
  .select-overlay {{
    position: absolute;
    inset: 0;
    pointer-events: none;
    transition: background 0.15s;
  }}
  .select-overlay.sel-pick   {{ background: rgba(34,197,94,0.35); }}
  .select-overlay.sel-hold   {{ background: rgba(245,158,11,0.35); }}
  .select-overlay.sel-reject {{ background: rgba(239,68,68,0.35); }}

  .select-badge {{
    position: absolute;
    top: 6px;
    right: 6px;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: none;
    background: rgba(255,255,255,0.1);
    color: #fff;
    font-size: 0.78rem;
    font-weight: 800;
    cursor: pointer;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.1s, background 0.15s;
    line-height: 1;
    padding: 0;
  }}
  .select-badge:hover {{ transform: scale(1.2); }}
  .select-badge.sel-pick   {{ background: #22c55e; }}
  .select-badge.sel-hold   {{ background: #f59e0b; }}
  .select-badge.sel-reject {{ background: #ef4444; }}

  /* pos-badge を左上に移動（select-badge と重ならないよう） */
  .pos-badge {{
    position: absolute;
    top: 6px;
    left: 6px;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.62rem;
    font-weight: 800;
    color: #fff;
    letter-spacing: 0.05em;
  }}

  /* サマリーバー */
  .select-summary {{
    display: flex;
    align-items: center;
    gap: 6px;
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 0.82rem;
    color: #64748b;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }}
  .sel-sum-label {{ color: #475569; margin-right: 4px; }}
  .sel-sum-pick   {{ color: #22c55e; font-weight: 700; }}
  .sel-sum-hold   {{ color: #f59e0b; font-weight: 700; }}
  .sel-sum-reject {{ color: #ef4444; font-weight: 700; }}
  .sel-sum-none   {{ color: #475569; }}
  .sel-sum-sep    {{ color: #1e2d45; }}

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
    max-height: 82vh;
    border-radius: 6px;
    box-shadow: 0 8px 40px rgba(0,0,0,0.7);
  }}
  .modal-footer {{
    display: flex;
    align-items: center;
    gap: 20px;
    flex-wrap: wrap;
    justify-content: center;
  }}
  .modal-name {{ color: #94a3b8; font-size: 0.82rem; }}
  .modal-hint {{ color: #475569; font-size: 0.72rem; }}
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

  /* セッション情報パネル */
  .session-panel {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 18px;
  }}
  .session-header {{
    display: flex;
    align-items: baseline;
    gap: 14px;
    margin-bottom: 6px;
  }}
  .session-title {{
    font-size: 1rem;
    font-weight: 700;
    color: #f1f5f9;
  }}
  .session-date {{
    font-size: 0.78rem;
    color: #64748b;
  }}
  .session-purpose {{
    font-size: 0.8rem;
    color: #94a3b8;
    margin-bottom: 10px;
  }}
  .session-memo-label {{
    font-size: 0.72rem;
    color: #64748b;
    margin-bottom: 5px;
  }}
  .session-note {{
    width: 100%;
    min-height: 72px;
    background: #0a0f1e;
    border: 1px solid #1e2d45;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: 0.82rem;
    font-family: inherit;
    padding: 8px 10px;
    resize: vertical;
    outline: none;
    transition: border-color 0.15s;
  }}
  .session-note:focus {{ border-color: #818cf8; }}
  .session-note::placeholder {{ color: #334155; }}

  /* スコアチューナーパネル */
  .tuner-panel {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 18px;
  }}
  .tuner-title {{
    font-size: 0.78rem;
    font-weight: 700;
    color: #818cf8;
    margin-bottom: 12px;
    letter-spacing: 0.04em;
  }}
  .tuner-rows {{
    display: grid;
    grid-template-columns: 100px 1fr 44px;
    align-items: center;
    gap: 8px 10px;
  }}
  .tuner-label {{
    font-size: 0.75rem;
    color: #94a3b8;
    text-align: right;
    white-space: nowrap;
  }}
  .tuner-slider {{
    -webkit-appearance: none;
    appearance: none;
    width: 100%;
    height: 4px;
    border-radius: 2px;
    background: #1e2d45;
    outline: none;
    cursor: pointer;
  }}
  .tuner-slider::-webkit-slider-thumb {{
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #818cf8;
    cursor: pointer;
    transition: background 0.15s;
  }}
  .tuner-slider::-webkit-slider-thumb:hover {{ background: #a5b4fc; }}
  .tuner-val {{
    font-size: 0.75rem;
    color: #f1f5f9;
    font-weight: 600;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
  .tuner-reset {{
    grid-column: 3;
    margin-top: 6px;
    padding: 4px 10px;
    border-radius: 12px;
    border: 1px solid #1e2d45;
    background: transparent;
    color: #64748b;
    font-size: 0.72rem;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }}
  .tuner-reset:hover {{ border-color: #818cf8; color: #818cf8; }}
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
    <div class="stat s-tech">
      <div class="n" style="color:{tech_avg_color}">{tech_avg:.2f}</div><div class="l">技術平均</div>
    </div>
    <div class="stat s-top10">
      <div class="n" style="color:#22c55e">{n_top10}</div><div class="l">上位10%</div>
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
{session_panel_html}
  <div class="tuner-panel">
    <div class="tuner-title">スコアチューナー</div>
    <div class="tuner-rows">
      <span class="tuner-label">フォーカス重み</span>
      <input class="tuner-slider" id="w-sharpness" type="range" min="0" max="1" step="0.05" value="0.50" oninput="onSlider('w-sharpness','v-sharpness')">
      <span class="tuner-val" id="v-sharpness">0.50</span>

      <span class="tuner-label">露出重み</span>
      <input class="tuner-slider" id="w-exposure" type="range" min="0" max="1" step="0.05" value="0.40" oninput="onSlider('w-exposure','v-exposure')">
      <span class="tuner-val" id="v-exposure">0.40</span>

      <span class="tuner-label">人物ボーナス</span>
      <input class="tuner-slider" id="w-persons" type="range" min="0" max="1" step="0.05" value="0.20" oninput="onSlider('w-persons','v-persons')">
      <span class="tuner-val" id="v-persons">0.20</span>

      <span class="tuner-label">初期衝動</span>
      <input class="tuner-slider" id="w-first" type="range" min="0" max="1" step="0.05" value="0.20" oninput="onSlider('w-first','v-first')">
      <span class="tuner-val" id="v-first">0.20</span>

      <span></span>
      <span></span>
      <button class="tuner-reset" onclick="resetTuner()">デフォルトに戻す</button>
    </div>
  </div>
  <div class="select-summary" id="select-summary">
    <span class="sel-sum-label">セレクト:</span>
    採用 <span class="sel-sum-pick" id="cnt-pick">0</span>
    <span class="sel-sum-sep">/</span>
    保留 <span class="sel-sum-hold" id="cnt-hold">0</span>
    <span class="sel-sum-sep">/</span>
    不採用 <span class="sel-sum-reject" id="cnt-reject">0</span>
    <span class="sel-sum-sep">/</span>
    未選択 <span class="sel-sum-none" id="cnt-none">{n_shots}</span>
  </div>
  <div class="counter" id="counter">表示: {n_groups} グループ</div>
  <div id="groups">
{groups_html}
  </div>
</div>

<!-- モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img id="modal-img" src="" alt="">
  <div class="modal-footer">
    <span class="modal-name" id="modal-name"></span>
    <span class="modal-hint">← → 同グループ内 &nbsp;|&nbsp; J/K 全体移動 &nbsp;|&nbsp; 1=✗ &nbsp;3=△ &nbsp;5=✓</span>
  </div>
</div>

<script>
// ---- フィルター ----
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

// ---- モーダル ----
const ALL_SHOTS = {all_shots_json};
let currentShotIdx = -1;

function openModal(url, name, gid) {{
  currentShotIdx = ALL_SHOTS.findIndex(s => s.stem === name);
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

function navigateModal(shot) {{
  currentShotIdx = ALL_SHOTS.findIndex(s => s.stem === shot.stem);
  document.getElementById('modal-img').src = shot.url;
  document.getElementById('modal-name').textContent = shot.stem;
}}

document.addEventListener('keydown', function(e) {{
  const modal = document.getElementById('modal');
  const isOpen = modal.classList.contains('open');

  if (!isOpen) return;

  if (e.key === 'Escape') {{ closeModal(); return; }}

  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {{
    e.preventDefault();
    if (currentShotIdx < 0) return;
    const curGid = ALL_SHOTS[currentShotIdx].gid;
    const groupShots = ALL_SHOTS.filter(s => s.gid === curGid);
    const groupIdx = groupShots.findIndex(s => s.stem === ALL_SHOTS[currentShotIdx].stem);
    const delta = e.key === 'ArrowRight' ? 1 : -1;
    const nextGroupIdx = groupIdx + delta;
    if (nextGroupIdx >= 0 && nextGroupIdx < groupShots.length) {{
      navigateModal(groupShots[nextGroupIdx]);
    }}
    return;
  }}

  if (e.key === 'j' || e.key === 'J') {{
    e.preventDefault();
    if (currentShotIdx < ALL_SHOTS.length - 1) navigateModal(ALL_SHOTS[currentShotIdx + 1]);
    return;
  }}
  if (e.key === 'k' || e.key === 'K') {{
    e.preventDefault();
    if (currentShotIdx > 0) navigateModal(ALL_SHOTS[currentShotIdx - 1]);
    return;
  }}

  if (e.key === '1') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'reject'); return; }}
  if (e.key === '3') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'hold');   return; }}
  if (e.key === '5') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'pick');   return; }}
}});

// ---- セレクト管理 ----
const SELECT_FILENAME = window.location.pathname.split('/').pop() || 'report.html';
const SELECT_KEY = 'asa-select-' + SELECT_FILENAME;
let selectState = {{}};

function loadSelectState() {{
  try {{
    const saved = localStorage.getItem(SELECT_KEY);
    selectState = saved ? JSON.parse(saved) : {{}};
  }} catch(e) {{ selectState = {{}}; }}
}}

function saveSelectState() {{
  localStorage.setItem(SELECT_KEY, JSON.stringify(selectState));
}}

function toggleSelect(e, stem) {{
  e.stopPropagation();
  const cycle = {{ 'pick': 'hold', 'hold': 'reject', 'reject': null }};
  const current = selectState[stem];
  const next = (current in cycle) ? cycle[current] : 'pick';
  setSelect(stem, next);
}}

function setSelect(stem, state) {{
  if (state === null || state === undefined) {{
    delete selectState[stem];
  }} else {{
    selectState[stem] = state;
  }}
  saveSelectState();
  updateCardVisual(stem);
  updateSummary();
}}

function updateCardVisual(stem) {{
  const overlay = document.getElementById('sol-' + stem);
  const badge   = document.getElementById('sbadge-' + stem);
  if (!overlay || !badge) return;

  const state = selectState[stem];
  overlay.className = 'select-overlay';
  badge.className   = 'select-badge';
  badge.textContent = '';

  if (state === 'pick')   {{ overlay.classList.add('sel-pick');   badge.classList.add('sel-pick');   badge.textContent = '✓'; }}
  if (state === 'hold')   {{ overlay.classList.add('sel-hold');   badge.classList.add('sel-hold');   badge.textContent = '△'; }}
  if (state === 'reject') {{ overlay.classList.add('sel-reject'); badge.classList.add('sel-reject'); badge.textContent = '✗'; }}
}}

function updateSummary() {{
  const total = ALL_SHOTS.length;
  let pick = 0, hold = 0, reject = 0;
  for (const v of Object.values(selectState)) {{
    if (v === 'pick')   pick++;
    else if (v === 'hold')   hold++;
    else if (v === 'reject') reject++;
  }}
  const none = total - pick - hold - reject;
  document.getElementById('cnt-pick').textContent   = pick;
  document.getElementById('cnt-hold').textContent   = hold;
  document.getElementById('cnt-reject').textContent = reject;
  document.getElementById('cnt-none').textContent   = none;
}}

// 初期化
loadSelectState();
ALL_SHOTS.forEach(s => updateCardVisual(s.stem));
updateSummary();

// ---- セッションメモ localStorage 自動保存 ----

// スコアチューナー
(function() {{
  const DEFAULTS = {{ sharpness: 0.50, exposure: 0.40, persons: 0.20, first: 0.20 }};
  const filename = window.location.pathname.split('/').pop() || 'index.html';
  const STORAGE_KEY = 'asa-weights-' + filename;

  function scoreColor(v) {{
    if (v >= 0.8) return '#22c55e';
    if (v >= 0.5) return '#f59e0b';
    return '#ef4444';
  }}

  function getWeights() {{
    return {{
      sharpness: parseFloat(document.getElementById('w-sharpness').value),
      exposure:  parseFloat(document.getElementById('w-exposure').value),
      persons:   parseFloat(document.getElementById('w-persons').value),
      first:     parseFloat(document.getElementById('w-first').value),
    }};
  }}

  function recompute() {{
    const w = getWeights();
    const total = w.sharpness + w.exposure + w.persons + w.first;
    document.querySelectorAll('.card').forEach(card => {{
      const sharp   = parseFloat(card.dataset.sharpness);
      const expo    = parseFloat(card.dataset.exposure);
      const p       = parseInt(card.dataset.persons);
      const isFirst = card.dataset.position === 'first';
      const raw = w.sharpness * sharp
                + w.exposure  * expo
                + w.persons   * Math.min(p / 3, 1.0)
                + w.first     * (isFirst ? 1.0 : 0.0);
      const normalized = total > 0 ? raw / total : 0;
      const el = card.querySelector('.tech-main');
      if (el) {{
        el.textContent = '技術 ' + normalized.toFixed(2);
        el.style.color = scoreColor(normalized);
      }}
    }});
  }}

  window.onSlider = function(sliderId, valId) {{
    const val = parseFloat(document.getElementById(sliderId).value);
    document.getElementById(valId).textContent = val.toFixed(2);
    recompute();
    try {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(getWeights())); }} catch(e) {{}}
  }};

  window.resetTuner = function() {{
    Object.entries(DEFAULTS).forEach(([k, v]) => {{
      document.getElementById('w-' + k).value = v;
      document.getElementById('v-' + k).textContent = v.toFixed(2);
    }});
    recompute();
    try {{ localStorage.removeItem(STORAGE_KEY); }} catch(e) {{}}
  }};

  // ページ読み込み時に復元
  try {{
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {{
      const weights = JSON.parse(saved);
      Object.entries(weights).forEach(([k, v]) => {{
        const slider = document.getElementById('w-' + k);
        const valEl  = document.getElementById('v-' + k);
        if (slider && valEl) {{
          slider.value = v;
          valEl.textContent = parseFloat(v).toFixed(2);
        }}
      }});
    }}
  }} catch(e) {{}}
  recompute();
}})();

// セッションメモ localStorage 自動保存
(function() {{
  const textarea = document.getElementById('session-note');
  if (!textarea) return;
  const filename = window.location.pathname.split('/').pop() || 'index.html';
  const storageKey = 'asa-session-note-' + filename;
  const initNote = {session_note_init};

  const saved = localStorage.getItem(storageKey);
  textarea.value = (saved !== null) ? saved : initNote;

  let timer;
  textarea.addEventListener('input', function() {{
    clearTimeout(timer);
    timer = setTimeout(function() {{
      localStorage.setItem(storageKey, textarea.value);
    }}, 500);
  }});
}})();
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
    parser.add_argument('--output',       default='stage2_report.html')
    parser.add_argument('--session-json', default=None,
                        help='セッション情報JSONファイル (省略可)')
    args = parser.parse_args()

    jpeg_dir  = Path(args.jpeg_dir)
    csv_path  = Path(args.stage2_csv)

    for p, name in [(jpeg_dir, 'JPEGフォルダ'), (csv_path, 'CSV')]:
        if not p.exists():
            print(f'エラー: {name} が見つかりません: {p}')
            sys.exit(1)

    session_info = None
    if args.session_json:
        session_json_path = Path(args.session_json)
        if not session_json_path.exists():
            print(f'エラー: session-json が見つかりません: {session_json_path}')
            sys.exit(1)
        with open(session_json_path, encoding='utf-8') as f:
            session_info = json.load(f)

    rows = load_groups(csv_path)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = csv_path.parent / output_path

    html_str = generate_html(rows, jpeg_dir, session_info)
    output_path.write_text(html_str, encoding='utf-8')

    n_groups = max(r['group_id'] for r in rows) + 1
    print(f'レポート生成完了: {output_path}')
    print(f'グループ数: {n_groups} / ショット数: {len(rows)}')
    print(f'\n  open "{output_path}"')


if __name__ == '__main__':
    main()
