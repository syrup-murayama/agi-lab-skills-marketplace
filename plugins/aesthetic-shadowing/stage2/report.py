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

def tech_score_color(score: float) -> str:
    if score >= 0.8:
        return '#22c55e'
    elif score >= 0.5:
        return '#f59e0b'
    else:
        return '#ef4444'


def make_card(row: dict, jpeg_url: str) -> str:
    pos = row['position']
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

    return f'''<div class="card" tabindex="0" data-stem="{stem}" data-gid="{gid}" data-url="{jpeg_url}"
  data-sharpness="{sharp:.3f}" data-exposure="{expo:.3f}"
  data-persons="{n_persons}" data-position="{pos}"
  onclick="openModal('{jpeg_url}','{stem}',{gid})"
  onkeydown="cardKeydown(event,this)">
  <div class="thumb-wrap">
    <img src="{jpeg_url}" alt="{stem}" loading="lazy">
    <div class="select-overlay" id="sol-{stem}"></div>
    <button class="select-badge" id="sbadge-{stem}" onclick="toggleSelect(event,'{stem}')" title="セレクト切り替え"></button>
    <button class="exclude-btn" id="excbtn-{stem}" onclick="toggleExclude(event,'{stem}')" title="除外">🚫</button>
    <div class="exclude-label" id="exclabel-{stem}" style="display:none">除外済み</div>
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
    sorted_rows = sorted(rows, key=lambda r: (r['group_id'], r['datetime'], r['stem']))
    groups_html_parts = []
    for gid, it in groupby(sorted_rows, key=lambda r: r['group_id']):
        members = list(it)
        groups_html_parts.append(make_group_section(gid, members, jpeg_dir))

    groups_html = '\n'.join(groups_html_parts)

    n_groups = max(r['group_id'] for r in rows) + 1
    n_shots  = len(rows)

    tech_scores = [r['technical_score'] for r in rows]
    tech_avg  = sum(tech_scores) / len(tech_scores) if tech_scores else 0.0
    top10_threshold = sorted(tech_scores, reverse=True)[max(0, len(tech_scores) // 10 - 1)]
    n_top10  = sum(1 for s in tech_scores if s >= top10_threshold)
    tech_avg_color = tech_score_color(tech_avg)

    session_panel_html = make_session_panel(session_info) if session_info else ''
    session_note_init = json.dumps(session_info.get('session_note', '') if session_info else '')

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
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  /* ヘッダー */
  .header {{
    background: #0f1829;
    border-bottom: 1px solid #1e2d45;
    padding: 12px 16px;
    flex-shrink: 0;
    z-index: 100;
  }}
  .header-top {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }}
  .hamburger {{
    background: none;
    border: 1px solid #1e2d45;
    color: #94a3b8;
    font-size: 1rem;
    cursor: pointer;
    padding: 4px 9px;
    border-radius: 6px;
    line-height: 1;
    flex-shrink: 0;
    transition: all 0.15s;
  }}
  .hamburger:hover {{ border-color: #818cf8; color: #818cf8; }}
  .header h1 {{ font-size: 1.05rem; color: #f1f5f9; flex: 1; }}
  .header h1 span {{ color: #818cf8; }}

  .stats {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }}
  .stat {{
    background: #0a0f1e;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    padding: 5px 12px;
    text-align: center;
    min-width: 68px;
  }}
  .stat .n {{ font-size: 1.2rem; font-weight: 700; }}
  .stat .l {{ font-size: 0.65rem; color: #64748b; margin-top: 2px; }}
  .s-group .n {{ color: #818cf8; }}
  .s-tech  .n {{ color: #22c55e; }}
  .s-top10 .n {{ color: #22c55e; }}

  .header-filters {{
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
  .filter-btn.f-multi.active {{ background: #22c55e; border-color: #22c55e; }}
  .filter-btn.f-solo.active  {{ background: #f59e0b; border-color: #f59e0b; }}
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

  /* 2段組レイアウト */
  .app-layout {{
    display: flex;
    flex: 1;
    overflow: hidden;
  }}

  /* サイドパネル */
  .sidebar {{
    width: 280px;
    flex-shrink: 0;
    overflow-y: auto;
    overflow-x: hidden;
    background: #080d1a;
    border-right: 1px solid #1e2d45;
    padding: 14px;
    transition: width 0.2s ease, padding 0.2s ease;
  }}
  .sidebar.collapsed {{
    width: 0;
    padding: 0;
    border-right: none;
  }}

  /* メインパネル */
  .main-panel {{
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }}

  .counter {{ color: #475569; font-size: 0.78rem; margin-bottom: 14px; }}

  /* グループセクション */
  .group {{
    margin-bottom: 24px;
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
  .gid     {{ font-weight: 700; color: #818cf8; min-width: 70px; }}
  .gsize   {{ color: #94a3b8; }}
  .gtime   {{ color: #64748b; }}
  .gtech   {{ font-size: 0.75rem; font-weight: 600; }}
  .gpersons {{ color: #64748b; margin-left: auto; }}

  .group-cards {{
    display: flex;
    flex-wrap: wrap;
    padding: 10px;
    gap: 8px;
  }}

  /* カード */
  .card {{
    width: 180px;
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    overflow: hidden;
    cursor: zoom-in;
    transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
    flex-shrink: 0;
    outline: none;
  }}
  .card:hover {{ transform: translateY(-2px); border-color: #818cf8; }}
  .card:focus-visible {{
    border-color: #818cf8;
    box-shadow: 0 0 0 2px #818cf8;
  }}
  .card.last-viewed {{
    border-color: #6366f1;
    box-shadow: 0 0 0 2px #6366f1;
  }}
  .card.excluded {{
    opacity: 0.38;
    filter: grayscale(0.7);
  }}

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

  .card-info {{ padding: 7px 9px; }}
  .card-name {{
    font-size: 0.65rem;
    color: #64748b;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-bottom: 3px;
  }}
  .card-meta {{
    display: flex;
    justify-content: space-between;
    font-size: 0.65rem;
    color: #475569;
    margin-bottom: 5px;
  }}
  .card-person {{ color: #94a3b8; }}
  .tech-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }}
  .tech-main {{ font-size: 0.78rem; font-weight: 700; }}
  .tech-sub  {{ font-size: 0.6rem; color: #475569; }}

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

  /* 除外ボタン・ラベル */
  .exclude-btn {{
    position: absolute;
    bottom: 6px;
    right: 6px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    border: none;
    background: rgba(0,0,0,0.55);
    font-size: 0.72rem;
    cursor: pointer;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    opacity: 0;
    transition: opacity 0.15s;
    line-height: 1;
  }}
  .card:hover .exclude-btn,
  .card.excluded .exclude-btn {{ opacity: 1; }}
  .exclude-label {{
    position: absolute;
    bottom: 6px;
    left: 6px;
    background: rgba(0,0,0,0.72);
    color: #94a3b8;
    font-size: 0.6rem;
    padding: 1px 5px;
    border-radius: 3px;
    pointer-events: none;
  }}

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

  /* ---- サイドバー内コンテンツ ---- */

  /* セッション情報パネル */
  .session-panel {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 14px;
  }}
  .session-header {{
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 5px;
    flex-wrap: wrap;
  }}
  .session-title {{
    font-size: 0.9rem;
    font-weight: 700;
    color: #f1f5f9;
  }}
  .session-date {{
    font-size: 0.72rem;
    color: #64748b;
  }}
  .session-purpose {{
    font-size: 0.75rem;
    color: #94a3b8;
    margin-bottom: 8px;
  }}
  .session-memo-label {{
    font-size: 0.7rem;
    color: #64748b;
    margin-bottom: 4px;
  }}
  .session-note {{
    width: 100%;
    min-height: 64px;
    background: #0a0f1e;
    border: 1px solid #1e2d45;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: 0.78rem;
    font-family: inherit;
    padding: 6px 8px;
    resize: vertical;
    outline: none;
    transition: border-color 0.15s;
  }}
  .session-note:focus {{ border-color: #818cf8; }}
  .session-note::placeholder {{ color: #334155; }}

  /* セレクトサマリー */
  .select-summary {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
  }}
  .sel-sum-label {{
    font-size: 0.7rem;
    color: #475569;
    display: block;
    margin-bottom: 8px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #818cf8;
  }}
  .sel-sum-row {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .sel-sum-item {{ display: flex; align-items: baseline; gap: 4px; }}
  .sel-sum-pick   {{ color: #22c55e; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-hold   {{ color: #f59e0b; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-reject {{ color: #ef4444; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-none   {{ color: #475569; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-sublabel {{ font-size: 0.68rem; color: #64748b; }}

  /* スコアチューナーパネル */
  .tuner-panel {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 14px;
  }}
  .tuner-title {{
    font-size: 0.75rem;
    font-weight: 700;
    color: #818cf8;
    margin-bottom: 10px;
    letter-spacing: 0.04em;
  }}
  .tuner-rows {{
    display: grid;
    grid-template-columns: 90px 1fr 40px;
    align-items: center;
    gap: 7px 8px;
  }}
  .tuner-label {{
    font-size: 0.72rem;
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
    width: 13px;
    height: 13px;
    border-radius: 50%;
    background: #818cf8;
    cursor: pointer;
    transition: background 0.15s;
  }}
  .tuner-slider::-webkit-slider-thumb:hover {{ background: #a5b4fc; }}
  .tuner-val {{
    font-size: 0.72rem;
    color: #f1f5f9;
    font-weight: 600;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
  .tuner-reset {{
    grid-column: 3;
    margin-top: 4px;
    padding: 3px 8px;
    border-radius: 10px;
    border: 1px solid #1e2d45;
    background: transparent;
    color: #64748b;
    font-size: 0.68rem;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }}
  .tuner-reset:hover {{ border-color: #818cf8; color: #818cf8; }}

  /* フィルタパネル */
  .filter-panel {{
    background: #0f1829;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 14px;
  }}
  .filter-panel-title {{
    font-size: 0.75rem;
    font-weight: 700;
    color: #818cf8;
    margin-bottom: 10px;
    letter-spacing: 0.04em;
  }}
  .filter-check-row {{
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 8px;
    font-size: 0.75rem;
    color: #94a3b8;
    cursor: pointer;
    user-select: none;
  }}
  .filter-check-row:last-child {{ margin-bottom: 0; }}
  .filter-check-row input[type="checkbox"] {{
    accent-color: #818cf8;
    width: 13px;
    height: 13px;
    cursor: pointer;
    flex-shrink: 0;
  }}

  @media (max-width: 899px) {{
    .sidebar {{ width: 260px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <button class="hamburger" onclick="toggleSidebar()" title="サイドパネル切り替え">☰</button>
    <h1>Aesthetic Shadowing Agent — <span>Stage 2 グループレポート</span></h1>
  </div>
  <div class="stats">
    <div class="stat s-group">
      <div class="n">{n_groups}</div><div class="l">グループ</div>
    </div>
    <div class="stat">
      <div class="n">{n_shots}</div><div class="l">総ショット</div>
    </div>
    <div class="stat s-tech">
      <div class="n" style="color:{tech_avg_color}">{tech_avg:.2f}</div><div class="l">技術平均</div>
    </div>
    <div class="stat s-top10">
      <div class="n">{n_top10}</div><div class="l">上位10%</div>
    </div>
  </div>
  <div class="header-filters">
    <button class="filter-btn active" onclick="setFilter('all',this)">全グループ</button>
    <button class="filter-btn f-multi" onclick="setFilter('multi',this)">複数枚のみ</button>
    <button class="filter-btn f-solo"  onclick="setFilter('solo',this)">SOLO のみ</button>
    <input  class="jump-input" id="jump-input"
            placeholder="Group番号へ…"
            onkeydown="if(event.key==='Enter')jumpTo(this.value)">
  </div>
</div>

<div class="app-layout">
  <aside class="sidebar" id="sidebar">
{session_panel_html}
    <div class="select-summary" id="select-summary">
      <span class="sel-sum-label">セレクト</span>
      <div class="sel-sum-row">
        <div class="sel-sum-item">
          <span class="sel-sum-pick" id="cnt-pick">0</span>
          <span class="sel-sum-sublabel">採用</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-hold" id="cnt-hold">0</span>
          <span class="sel-sum-sublabel">保留</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-reject" id="cnt-reject">0</span>
          <span class="sel-sum-sublabel">不採用</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-none" id="cnt-none">{n_shots}</span>
          <span class="sel-sum-sublabel">未選択</span>
        </div>
      </div>
    </div>

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

    <div class="filter-panel">
      <div class="filter-panel-title">フィルタ</div>
      <label class="filter-check-row">
        <input type="checkbox" id="f-hide-excluded" onchange="onFilterChange()">
        除外済みを非表示
      </label>
      <label class="filter-check-row">
        <input type="checkbox" id="f-top10" onchange="onFilterChange()">
        上位10%のみ表示
      </label>
      <label class="filter-check-row">
        <input type="checkbox" id="f-no-persons" onchange="onFilterChange()">
        人物なしを非表示
      </label>
    </div>
  </aside>

  <div class="main-panel">
    <div class="counter" id="counter">表示: {n_groups} グループ</div>
    <div id="groups">
{groups_html}
    </div>
  </div>
</div>

<!-- モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img id="modal-img" src="" alt="">
  <div class="modal-footer">
    <span class="modal-name" id="modal-name"></span>
    <span class="modal-hint">← → 同グループ内 &nbsp;|&nbsp; J/K 全体移動 &nbsp;|&nbsp; 1=✗ &nbsp;3=△ &nbsp;5=✓ &nbsp;|&nbsp; x=除外 &nbsp;u=解除</span>
  </div>
</div>

<script>
const _FILENAME = window.location.pathname.split('/').pop() || 'report.html';

// ---- サイドパネル ----
const SIDEBAR_KEY = 'asa-sidebar-' + _FILENAME;

function toggleSidebar() {{
  const sidebar = document.getElementById('sidebar');
  const isCollapsed = sidebar.classList.toggle('collapsed');
  try {{ localStorage.setItem(SIDEBAR_KEY, isCollapsed ? 'closed' : 'open'); }} catch(e) {{}}
}}

(function initSidebar() {{
  const saved = localStorage.getItem(SIDEBAR_KEY);
  const sidebar = document.getElementById('sidebar');
  if (saved !== null) {{
    sidebar.classList.toggle('collapsed', saved === 'closed');
  }} else if (window.innerWidth < 900) {{
    sidebar.classList.add('collapsed');
  }}
}})();

// ---- グループフィルター ----
function setFilter(type, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.group').forEach(g => {{
    const size = parseInt(g.querySelector('.gsize').textContent);
    const show = (type === 'all') || (type === 'multi' && size > 1) || (type === 'solo' && size === 1);
    g.classList.toggle('hidden', !show);
  }});
  const visible = document.querySelectorAll('.group:not(.hidden)').length;
  document.getElementById('counter').textContent = '表示: ' + visible + ' グループ';
  applyAllFilters();
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
let lastViewedStem = null;

function openModal(url, name, gid) {{
  document.querySelectorAll('.card.last-viewed').forEach(c => c.classList.remove('last-viewed'));
  lastViewedStem = name;
  currentShotIdx = ALL_SHOTS.findIndex(s => s.stem === name);
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = name;
  document.getElementById('modal').classList.add('open');
}}

function closeModalAction() {{
  document.getElementById('modal').classList.remove('open');
  document.getElementById('modal-img').src = '';
  if (lastViewedStem) {{
    const card = document.querySelector('.card[data-stem="' + lastViewedStem + '"]');
    if (card) {{
      card.classList.add('last-viewed');
      card.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
    }}
  }}
}}

function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal')) {{
    closeModalAction();
  }}
}}

function navigateModal(shot) {{
  lastViewedStem = shot.stem;
  currentShotIdx = ALL_SHOTS.findIndex(s => s.stem === shot.stem);
  document.getElementById('modal-img').src = shot.url;
  document.getElementById('modal-name').textContent = shot.stem;
}}

document.addEventListener('keydown', function(e) {{
  const modal = document.getElementById('modal');
  const isOpen = modal.classList.contains('open');
  if (!isOpen) return;

  if (e.key === 'Escape') {{ closeModalAction(); return; }}

  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {{
    e.preventDefault();
    if (currentShotIdx < 0) return;
    const curGid = ALL_SHOTS[currentShotIdx].gid;
    const groupShots = ALL_SHOTS.filter(s => s.gid === curGid);
    const groupIdx = groupShots.findIndex(s => s.stem === ALL_SHOTS[currentShotIdx].stem);
    const delta = e.key === 'ArrowRight' ? 1 : -1;
    const next = groupIdx + delta;
    if (next >= 0 && next < groupShots.length) navigateModal(groupShots[next]);
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
  if (e.key === 'x' || e.key === 'X') {{
    if (currentShotIdx >= 0) setExcluded(ALL_SHOTS[currentShotIdx].stem);
    return;
  }}
  if (e.key === 'u' || e.key === 'U') {{
    if (currentShotIdx >= 0) unsetExcluded(ALL_SHOTS[currentShotIdx].stem);
    return;
  }}
}});

// ---- グリッドキーボード操作 ----
function cardKeydown(e, card) {{
  if (document.getElementById('modal').classList.contains('open')) return;
  const stem = card.dataset.stem;
  if (e.key === 'Enter' || e.key === ' ') {{
    e.preventDefault();
    openModal(card.dataset.url, stem, parseInt(card.dataset.gid));
    return;
  }}
  if (e.key === '1') {{ e.preventDefault(); setSelect(stem, 'reject'); return; }}
  if (e.key === '3') {{ e.preventDefault(); setSelect(stem, 'hold');   return; }}
  if (e.key === '5') {{ e.preventDefault(); setSelect(stem, 'pick');   return; }}
  if (e.key === 'x' || e.key === 'X') {{ e.preventDefault(); setExcluded(stem); return; }}
  if (e.key === 'u' || e.key === 'U') {{ e.preventDefault(); unsetExcluded(stem); return; }}
}}

// ---- セレクト管理 ----
const SELECT_KEY = 'asa-select-' + _FILENAME;
let selectState = {{}};

function loadSelectState() {{
  try {{
    const saved = localStorage.getItem(SELECT_KEY);
    selectState = saved ? JSON.parse(saved) : {{}};
  }} catch(e) {{ selectState = {{}}; }}
}}

function saveSelectState() {{
  try {{ localStorage.setItem(SELECT_KEY, JSON.stringify(selectState)); }} catch(e) {{}}
}}

function toggleSelect(e, stem) {{
  if (e) e.stopPropagation();
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
    if (v === 'pick')        pick++;
    else if (v === 'hold')   hold++;
    else if (v === 'reject') reject++;
  }}
  document.getElementById('cnt-pick').textContent   = pick;
  document.getElementById('cnt-hold').textContent   = hold;
  document.getElementById('cnt-reject').textContent = reject;
  document.getElementById('cnt-none').textContent   = total - pick - hold - reject;
}}

// ---- 除外フラグ ----
const EXCLUDE_KEY = 'asa-exclude-' + _FILENAME;
let excludeState = {{}};

function loadExcludeState() {{
  try {{
    const saved = localStorage.getItem(EXCLUDE_KEY);
    excludeState = saved ? JSON.parse(saved) : {{}};
  }} catch(e) {{ excludeState = {{}}; }}
}}

function saveExcludeState() {{
  try {{ localStorage.setItem(EXCLUDE_KEY, JSON.stringify(excludeState)); }} catch(e) {{}}
}}

function setExcluded(stem) {{
  excludeState[stem] = true;
  saveExcludeState();
  updateCardExcludeVisual(stem);
  applyAllFilters();
}}

function unsetExcluded(stem) {{
  delete excludeState[stem];
  saveExcludeState();
  updateCardExcludeVisual(stem);
  applyAllFilters();
}}

function toggleExclude(e, stem) {{
  if (e) e.stopPropagation();
  if (excludeState[stem]) {{ unsetExcluded(stem); }} else {{ setExcluded(stem); }}
}}

function updateCardExcludeVisual(stem) {{
  const card = document.querySelector('.card[data-stem="' + stem + '"]');
  if (!card) return;
  const isExcluded = !!excludeState[stem];
  card.classList.toggle('excluded', isExcluded);
  const label = document.getElementById('exclabel-' + stem);
  if (label) label.style.display = isExcluded ? '' : 'none';
}}

// ---- フィルタ ----
const FILTERS_KEY = 'asa-filters-' + _FILENAME;
let filterState = {{ hideExcluded: false, top10Only: false, hideNoPersons: false }};

function loadFilters() {{
  try {{
    const saved = localStorage.getItem(FILTERS_KEY);
    if (saved) filterState = Object.assign(filterState, JSON.parse(saved));
  }} catch(e) {{}}
  document.getElementById('f-hide-excluded').checked = filterState.hideExcluded;
  document.getElementById('f-top10').checked          = filterState.top10Only;
  document.getElementById('f-no-persons').checked     = filterState.hideNoPersons;
}}

function saveFilters() {{
  try {{ localStorage.setItem(FILTERS_KEY, JSON.stringify(filterState)); }} catch(e) {{}}
}}

function onFilterChange() {{
  filterState.hideExcluded  = document.getElementById('f-hide-excluded').checked;
  filterState.top10Only     = document.getElementById('f-top10').checked;
  filterState.hideNoPersons = document.getElementById('f-no-persons').checked;
  saveFilters();
  applyAllFilters();
}}

function applyAllFilters() {{
  const cards = Array.from(document.querySelectorAll('.card'));
  let top10Threshold = -Infinity;
  if (filterState.top10Only && cards.length > 0) {{
    const scores = cards.map(c => parseFloat(c.dataset.computedScore || c.dataset.sharpness || '0'));
    scores.sort((a, b) => b - a);
    const idx = Math.max(0, Math.floor(scores.length * 0.1) - 1);
    top10Threshold = scores[idx] !== undefined ? scores[idx] : 0;
  }}
  cards.forEach(card => {{
    const stem = card.dataset.stem;
    let visible = true;
    if (filterState.hideExcluded && excludeState[stem]) visible = false;
    if (filterState.hideNoPersons && parseInt(card.dataset.persons) === 0) visible = false;
    if (filterState.top10Only) {{
      const score = parseFloat(card.dataset.computedScore || card.dataset.sharpness || '0');
      if (score < top10Threshold) visible = false;
    }}
    card.style.display = visible ? '' : 'none';
  }});
}}

// ---- スコアチューナー ----
(function() {{
  const DEFAULTS = {{ sharpness: 0.50, exposure: 0.40, persons: 0.20, first: 0.20 }};
  const STORAGE_KEY = 'asa-weights-' + _FILENAME;

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
      card.dataset.computedScore = normalized.toFixed(4);
      const el = card.querySelector('.tech-main');
      if (el) {{
        el.textContent = '技術 ' + normalized.toFixed(2);
        el.style.color = scoreColor(normalized);
      }}
    }});
    applyAllFilters();
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

// ---- セッションメモ localStorage 自動保存 ----
(function() {{
  const textarea = document.getElementById('session-note');
  if (!textarea) return;
  const storageKey = 'asa-session-note-' + _FILENAME;
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

// ---- 初期化 ----
loadSelectState();
loadExcludeState();
loadFilters();
ALL_SHOTS.forEach(s => updateCardVisual(s.stem));
ALL_SHOTS.forEach(s => updateCardExcludeVisual(s.stem));
updateSummary();
applyAllFilters();
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
