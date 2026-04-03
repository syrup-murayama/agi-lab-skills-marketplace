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
import threading
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
                'eye_score':       float(row['eye_score']) if row.get('eye_score', '') != '' else None,
                'sharpness_score': float(row.get('sharpness_score', 0.0)),
                'exposure_score':  float(row.get('exposure_score', 0.0)),
                'technical_score': float(row.get('technical_score', 0.0)),
                'camera_rating':   int(float(row.get('camera_rating', 0) or 0)),
                'near_rated':      row.get('near_rated', 'False').lower() == 'true',
            })
    return rows


def find_jpeg(jpeg_dir: Path, stem: str, img_base: str = 'file') -> str:
    for ext in ('.JPG', '.jpg', '.jpeg', '.JPEG'):
        p = jpeg_dir / (stem + ext)
        if p.exists():
            if img_base == 'file':
                return p.as_uri()
            return f'{img_base}/{stem}{ext}'
    return ''


# ---------- カード生成 ----------

def tech_score_color(score: float) -> str:
    if score >= 0.8:
        return '#22c55e'
    elif score >= 0.5:
        return '#f59e0b'
    else:
        return '#ef4444'


def make_card(row: dict, jpeg_url: str, has_camera_rating: bool = False) -> str:
    pos = row['position']
    stem = html.escape(row['stem'])
    dt = row['datetime'][11:19] if len(row['datetime']) >= 19 else ''
    n_persons = row['person_count']
    person_icon = '👤' if n_persons == 1 else ('👥' if n_persons >= 2 else '─')
    person_label = f'{n_persons}人' if n_persons > 0 else '0人'

    eye_score = row.get('eye_score')
    if eye_score is None:
        eye_icon = ''
    elif eye_score == 1.0:
        eye_icon = ' 👁️'
    elif eye_score > 0.0:
        eye_icon = ' 👁️ △'
    else:
        eye_icon = ' 😑'

    tech  = row['technical_score']
    sharp = row['sharpness_score']
    expo  = row['exposure_score']
    tech_color = tech_score_color(tech)
    gid = row['group_id']
    bonus = row.get("bonus_weight", 1.0)

    camera_rating_html = ''
    if has_camera_rating:
        cam_rating = row.get('camera_rating', 0)
        near_rated = row.get('near_rated', False)
        if cam_rating > 0:
            camera_rating_html = f'<span class="cam-rating">撮影時 ★{cam_rating}</span>'
        elif near_rated:
            camera_rating_html = '<span class="cam-near">📷±</span>'

    eye_val = f'{eye_score:.3f}' if eye_score is not None else '-1'

    return f'''<div class="card" tabindex="0" data-stem="{stem}" data-gid="{gid}" data-url="{jpeg_url}"
  data-sharpness="{sharp:.3f}" data-exposure="{expo:.3f}" data-eye="{eye_val}"
  data-persons="{n_persons}" data-position="{pos}" data-bonus="{bonus:.3f}"
  onclick="openModal('{jpeg_url}','{stem}',{gid})"
  onkeydown="cardKeydown(event,this)">
  <div class="thumb-wrap">
    <img src="{jpeg_url}" alt="{stem}" loading="lazy">
    <div class="select-overlay" id="sol-{stem}"></div>
    <button class="select-badge" id="sbadge-{stem}" onclick="toggleSelect(event,'{stem}')" title="セレクト切り替え"></button>
    <button class="info-btn" onclick="showInfo(event,'{stem}')" title="スコア内訳">ℹ</button>
    <button class="exclude-btn" id="excbtn-{stem}" onclick="toggleExclude(event,'{stem}')" title="除外">🚫</button>
    <button class="gap-btn" id="gapbtn-{stem}" onclick="openGapModal(event,'{stem}')" title="ギャップを報告">🚩</button>
    <div class="gap-flag-label" id="gaplabel-{stem}" style="display:none">🚩</div>
    <div class="exclude-label" id="exclabel-{stem}" style="display:none">除外済み</div>
  </div>
  <div class="card-info">
    <div class="card-name">{stem}</div>
    <div class="card-meta">
      <span class="card-time">{dt}</span>
      <span class="card-person">{person_icon} {person_label}{eye_icon}</span>
      {camera_rating_html}
    </div>
    <div class="tech-row">
      <span class="tech-main" style="color:{tech_color}">技術 {tech:.2f}</span>
      <span class="tech-sub">鮮 {sharp:.2f} / 露 {expo:.2f}</span>
    </div>
  </div>
</div>'''


def make_group_section(gid: int, members: list[dict], jpeg_dir: Path, has_camera_rating: bool = False, img_base: str = 'file') -> str:
    size = members[0]['group_size']
    dt_start = members[0]['datetime'][11:19] if members[0]['datetime'] else '?'
    n_persons_avg = sum(m['person_count'] for m in members) / len(members)
    max_tech = max(m['technical_score'] for m in members)
    max_tech_color = tech_score_color(max_tech)

    cards = '\n'.join(
        make_card(m, find_jpeg(jpeg_dir, m['stem'], img_base), has_camera_rating)
        for m in members
    )

    person_summary = f'{n_persons_avg:.1f}人/枚'
    cam_marker = ''
    if has_camera_rating and any(m.get('camera_rating', 0) > 0 for m in members):
        cam_marker = ' <span class="gcam">📷</span>'

    return f'''
<section class="group" id="g{gid}">
  <div class="group-header">
    <span class="gid">Group {gid}</span>
    <span class="gsize">{size}枚</span>
    <span class="gtime">{dt_start}</span>
    <span class="gtech" style="color:{max_tech_color}">最高技術 {max_tech:.2f}</span>
    <span class="gpersons">👤 {person_summary}</span>{cam_marker}
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


def generate_html(rows: list[dict], jpeg_dir: Path, session_info: dict | None = None,
                  img_base: str = 'file', server_mode: bool = False) -> str:
    has_camera_rating = any(r.get('camera_rating', 0) > 0 for r in rows)

    sorted_rows = sorted(rows, key=lambda r: (r['group_id'], r['datetime'], r['stem']))
    groups_html_parts = []
    for gid, it in groupby(sorted_rows, key=lambda r: r['group_id']):
        members = list(it)
        groups_html_parts.append(make_group_section(gid, members, jpeg_dir, has_camera_rating, img_base))

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
        [{'url': find_jpeg(jpeg_dir, r['stem'], img_base), 'stem': r['stem'], 'gid': r['group_id']}
         for r in sorted_rows],
        ensure_ascii=False,
    )
    server_mode_js = 'true' if server_mode else 'false'
    confirm_btn_display = '' if server_mode else 'display:none'
    export_btn_display = '' if server_mode else 'display:none'
    shutdown_btn_display = "" if server_mode else "display:none"

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
    position: relative;
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
  .cam-rating {{ color: #ffd700; font-size: 0.65rem; font-weight: 600; }}
  .cam-near   {{ color: #64748b; font-size: 0.6rem; }}
  .gcam {{ font-size: 0.78rem; }}
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
  .select-overlay.sel-good {{ background: rgba(34,197,94,0.35); }}
  .select-overlay.sel-fine {{ background: rgba(245,158,11,0.35); }}
  .select-overlay.sel-keep {{ background: rgba(96,165,250,0.35); }}

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
  .select-badge.sel-good {{ background: #22c55e; }}
  .select-badge.sel-fine {{ background: #f59e0b; }}
  .select-badge.sel-keep {{ background: #60a5fa; }}

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
  .sel-sum-good {{ color: #22c55e; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-fine {{ color: #f59e0b; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-keep {{ color: #60a5fa; font-weight: 700; font-size: 1.1rem; }}
  .sel-sum-none {{ color: #475569; font-weight: 700; font-size: 1.1rem; }}
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
  .tuner-confirm {{
    grid-column: 3;
    margin-top: 4px;
    padding: 3px 8px;
    border-radius: 10px;
    border: 1px solid #1e4d3a;
    background: transparent;
    color: #22c55e;
    font-size: 0.68rem;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }}
  .tuner-confirm:hover {{ border-color: #22c55e; background: #0d2a1f; }}
  .tuner-confirm:disabled {{ opacity: 0.5; cursor: default; }}

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

  /* 書き出しボタン */
  .export-btn {{
    display: block;
    width: 100%;
    margin-top: 10px;
    padding: 7px 12px;
    border-radius: 8px;
    border: 1px solid #1e4d3a;
    background: transparent;
    color: #22c55e;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
  }}
  .export-btn:hover {{ border-color: #22c55e; background: #0d2a1f; }}
  .export-btn:disabled {{ opacity: 0.5; cursor: default; }}

  /* ---- 情報ポップアップ ---- */
  .info-btn {{
    position: absolute;
    top: 6px;
    left: 6px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: none;
    background: rgba(0,0,0,0.55);
    color: #94a3b8;
    font-size: 0.7rem;
    font-style: italic;
    font-weight: 700;
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
  .card:hover .info-btn {{ opacity: 1; }}
  .info-popup {{
    display: none;
    position: fixed;
    background: #0f1829;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 0.72rem;
    color: #94a3b8;
    z-index: 2000;
    min-width: 210px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.6);
    pointer-events: none;
  }}
  .info-popup.visible {{ display: block; }}
  .info-popup-title {{ color: #818cf8; font-weight: 700; margin-bottom: 6px; font-size: 0.8rem; }}
  .info-popup-row {{ display: flex; justify-content: space-between; gap: 12px; margin-bottom: 3px; }}
  .info-popup-label {{ color: #64748b; }}
  .info-popup-val {{ color: #f1f5f9; font-variant-numeric: tabular-nums; }}
  .info-popup-divider {{ border: none; border-top: 1px solid #1e2d45; margin: 5px 0; }}

  /* ---- ギャップフラグ ---- */
  .gap-btn {{
    position: absolute;
    bottom: 30px;
    right: 6px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    border: none;
    background: rgba(0,0,0,0.55);
    font-size: 0.7rem;
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
  .card:hover .gap-btn,
  .card.gap-flagged .gap-btn {{ opacity: 1; }}
  .gap-flag-label {{
    position: absolute;
    top: 6px;
    left: 32px;
    font-size: 0.7rem;
    pointer-events: none;
    text-shadow: 0 1px 3px rgba(0,0,0,0.8);
  }}
  .card.gap-flagged {{ border-color: #ef4444 !important; box-shadow: 0 0 0 1px rgba(239,68,68,0.35); }}
  .gap-modal {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 1500; align-items: center; justify-content: center; }}
  .gap-modal.open {{ display: flex; }}
  .gap-modal-box {{ background: #0f1829; border: 1px solid #2d1e1e; border-radius: 12px; padding: 20px; width: 380px; max-width: 92vw; }}
  .gap-modal-title {{ font-size: 0.88rem; font-weight: 700; color: #f1f5f9; margin-bottom: 3px; }}
  .gap-modal-stem {{ font-size: 0.7rem; color: #64748b; margin-bottom: 12px; }}
  .gap-modal-label {{ font-size: 0.72rem; color: #94a3b8; margin-bottom: 6px; }}
  .gap-modal-textarea {{
    width: 100%; min-height: 80px; background: #0a0f1e; border: 1px solid #1e2d45;
    border-radius: 6px; color: #e2e8f0; font-size: 0.8rem; font-family: inherit;
    padding: 8px; resize: vertical; outline: none; transition: border-color 0.15s;
    margin-bottom: 12px; box-sizing: border-box;
  }}
  .gap-modal-textarea:focus {{ border-color: #ef4444; }}
  .gap-modal-textarea::placeholder {{ color: #334155; }}
  .gap-modal-actions {{ display: flex; gap: 8px; justify-content: flex-end; }}
  .gap-modal-cancel {{ padding: 6px 14px; border-radius: 6px; border: 1px solid #1e2d45; background: transparent; color: #64748b; font-size: 0.78rem; cursor: pointer; }}
  .gap-modal-cancel:hover {{ border-color: #475569; color: #94a3b8; }}
  .gap-modal-remove {{ padding: 6px 14px; border-radius: 6px; border: 1px solid #7f1d1d; background: transparent; color: #ef4444; font-size: 0.78rem; cursor: pointer; }}
  .gap-modal-remove:hover {{ background: #1f0a0a; }}
  .gap-modal-save {{ padding: 6px 14px; border-radius: 6px; border: 1px solid #991b1b; background: #450a0a; color: #fca5a5; font-size: 0.78rem; font-weight: 600; cursor: pointer; }}
  .gap-modal-save:hover {{ background: #7f1d1d; border-color: #ef4444; color: #fff; }}
  .gap-panel {{ background: #0f1829; border: 1px solid #2d1e1e; border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }}
  .gap-panel-title {{ font-size: 0.75rem; font-weight: 700; color: #ef4444; margin-bottom: 8px; letter-spacing: 0.04em; }}
  .gap-count-row {{ display: flex; align-items: baseline; gap: 6px; margin-bottom: 8px; }}
  .gap-count-n {{ font-size: 1.1rem; font-weight: 700; color: #ef4444; }}
  .gap-count-label {{ font-size: 0.7rem; color: #64748b; }}
  .gap-export-btn {{ display: block; width: 100%; padding: 6px 10px; border-radius: 6px; border: 1px solid #7f1d1d; background: transparent; color: #ef4444; font-size: 0.75rem; font-weight: 600; cursor: pointer; text-align: center; }}
  .gap-export-btn:hover {{ background: #1f0a0a; border-color: #ef4444; }}
  .gap-export-btn:disabled {{ opacity: 0.5; cursor: default; }}
  .shutdown-btn {{ background: none; border: 1px solid #7f1d1d; color: #ef4444; font-size: 0.85rem; cursor: pointer; padding: 3px 8px; border-radius: 6px; line-height: 1; flex-shrink: 0; transition: all 0.15s; }}
  .shutdown-btn:hover {{ background: #1f0a0a; border-color: #ef4444; }}

  @media (max-width: 899px) {{
    .sidebar {{ width: 260px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <button class="hamburger" onclick="toggleSidebar()" title="サイドパネル切り替え">☰</button>
    <button class="shutdown-btn" onclick="shutdownServer()" title="サーバーを停止" style="{shutdown_btn_display}">🔴</button>
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
      <div class="n" id="stat-top10">{n_top10}</div><div class="l">上位10%</div>
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
          <span class="sel-sum-good" id="cnt-good">0</span>
          <span class="sel-sum-sublabel">GOOD</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-fine" id="cnt-fine">0</span>
          <span class="sel-sum-sublabel">FINE</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-keep" id="cnt-keep">0</span>
          <span class="sel-sum-sublabel">KEEP</span>
        </div>
        <div class="sel-sum-item">
          <span class="sel-sum-none" id="cnt-none">{n_shots}</span>
          <span class="sel-sum-sublabel">未選択</span>
        </div>
      </div>
      <button class="export-btn" id="btn-export-selections"
              onclick="exportSelections()" style="{export_btn_display}">
        📤 XMPに書き出す
      </button>
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

        <span class="tuner-label">瞳ボーナス</span>
        <input class="tuner-slider" id="w-eye" type="range" min="0" max="1" step="0.05" value="0.20" oninput="onSlider('w-eye','v-eye')">
        <span class="tuner-val" id="v-eye">0.20</span>

        <span class="tuner-label">人物ボーナス</span>
        <input class="tuner-slider" id="w-persons" type="range" min="0" max="1" step="0.05" value="0.20" oninput="onSlider('w-persons','v-persons')">
        <span class="tuner-val" id="v-persons">0.20</span>

        <span class="tuner-label">初期衝動</span>
        <input class="tuner-slider" id="w-first" type="range" min="0" max="1" step="0.05" value="0.20" oninput="onSlider('w-first','v-first')">
        <span class="tuner-val" id="v-first">0.20</span>

        <span></span>
        <span></span>
        <button class="tuner-reset" onclick="resetTuner()">デフォルトに戻す</button>
        <span></span>
        <span></span>
        <button class="tuner-confirm" id="btn-confirm-weights" onclick="confirmWeights()" style="{confirm_btn_display}">このウェイトで確定</button>
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
    <div class="gap-panel">
      <div class="gap-panel-title">ギャップレポート</div>
      <div class="gap-count-row">
        <span class="gap-count-n" id="gap-count">0</span>
        <span class="gap-count-label">件フラグ済み</span>
      </div>
      <button class="gap-export-btn" id="btn-export-gaps"
              onclick="exportGaps()" style="{export_btn_display}">
        📊 ギャップをエクスポート
      </button>
    </div>
  </aside>

  <div class="main-panel">
    <div class="counter" id="counter">表示: {n_groups} グループ</div>
    <div id="groups">
{groups_html}
    </div>
  </div>
</div>

<div class="info-popup" id="info-popup"></div>

<!-- ギャップモーダル -->
<div class="gap-modal" id="gap-modal">
  <div class="gap-modal-box">
    <div class="gap-modal-title">🚩 スコアギャップを報告</div>
    <div class="gap-modal-stem" id="gap-modal-stem"></div>
    <div class="gap-modal-label">なぜスコアと感覚がずれているか（自由記述）:</div>
    <textarea class="gap-modal-textarea" id="gap-modal-memo"
              placeholder="例: 瞳が開いているのにeye_scoreが低い、人物がいないのにperson_countが1、など"></textarea>
    <div class="gap-modal-actions">
      <button class="gap-modal-cancel" onclick="closeGapModal()">キャンセル</button>
      <button class="gap-modal-remove" id="gap-modal-remove" onclick="removeGap()" style="display:none">フラグを削除</button>
      <button class="gap-modal-save" onclick="saveGap()">保存</button>
    </div>
  </div>
</div>

<!-- モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img id="modal-img" src="" alt="">
  <div class="modal-footer">
    <span class="modal-name" id="modal-name"></span>
    <span class="modal-hint">← → 同グループ内 &nbsp;|&nbsp; J/K 全体移動 &nbsp;|&nbsp; 1=KEEP &nbsp;2=FINE &nbsp;3=GOOD &nbsp;|&nbsp; X=除外 &nbsp;U=解除</span>
  </div>
</div>

<script>
const _FILENAME = window.location.pathname.split('/').pop() || 'report.html';
const _SERVER_MODE = {server_mode_js};

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

  if (e.key === '1') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'keep'); return; }}
  if (e.key === '2') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'fine'); return; }}
  if (e.key === '3') {{ if (currentShotIdx >= 0) setSelect(ALL_SHOTS[currentShotIdx].stem, 'good'); return; }}
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
  if (e.key === '1') {{ e.preventDefault(); setSelect(stem, 'keep'); return; }}
  if (e.key === '2') {{ e.preventDefault(); setSelect(stem, 'fine'); return; }}
  if (e.key === '3') {{ e.preventDefault(); setSelect(stem, 'good'); return; }}
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
  const cycle = {{ 'good': 'fine', 'fine': 'keep', 'keep': null }};
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
  if (state === 'good') {{ overlay.classList.add('sel-good'); badge.classList.add('sel-good'); badge.textContent = '3'; }}
  if (state === 'fine') {{ overlay.classList.add('sel-fine'); badge.classList.add('sel-fine'); badge.textContent = '2'; }}
  if (state === 'keep') {{ overlay.classList.add('sel-keep'); badge.classList.add('sel-keep'); badge.textContent = '1'; }}
}}

function updateSummary() {{
  const total = ALL_SHOTS.length;
  let good = 0, fine = 0, keep = 0;
  for (const v of Object.values(selectState)) {{
    if (v === 'good')      good++;
    else if (v === 'fine') fine++;
    else if (v === 'keep') keep++;
  }}
  document.getElementById('cnt-good').textContent = good;
  document.getElementById('cnt-fine').textContent = fine;
  document.getElementById('cnt-keep').textContent = keep;
  document.getElementById('cnt-none').textContent = total - good - fine - keep;
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
  const DEFAULTS = {{ sharpness: 0.50, exposure: 0.40, eye: 0.20, persons: 0.20, first: 0.20 }};
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
      eye:       parseFloat(document.getElementById('w-eye').value),
      persons:   parseFloat(document.getElementById('w-persons').value),
      first:     parseFloat(document.getElementById('w-first').value),
    }};
  }}

  function recompute() {{
    const w = getWeights();
    const total = w.sharpness + w.exposure + w.eye + w.persons + w.first;
    document.querySelectorAll('.card').forEach(card => {{
      const sharp   = parseFloat(card.dataset.sharpness);
      const expo    = parseFloat(card.dataset.exposure);
      const eyeRaw  = parseFloat(card.dataset.eye);
      const eyeVal  = eyeRaw >= 0 ? eyeRaw : 0;   // -1 は顔なし → 0扱い
      const p       = parseInt(card.dataset.persons);
      const isFirst = card.dataset.position === 'first';
      const raw = w.sharpness * sharp
                + w.exposure  * expo
                + w.eye       * eyeVal
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

    // ヘッダーの「上位10%」カウントを動的更新
    const allCards = Array.from(document.querySelectorAll('.card'));
    if (allCards.length > 0) {{
      const scores = allCards.map(c => parseFloat(c.dataset.computedScore || '0'));
      scores.sort((a, b) => b - a);
      const idx = Math.max(0, Math.floor(scores.length * 0.1) - 1);
      const threshold = scores[idx] !== undefined ? scores[idx] : 0;
      const count = scores.filter(s => s >= threshold).length;
      const el = document.getElementById('stat-top10');
      if (el) el.textContent = count;
    }}

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

  window.confirmWeights = function() {{
    if (!_SERVER_MODE) return;
    const w = getWeights();
    const btn = document.getElementById('btn-confirm-weights');
    if (btn) {{ btn.disabled = true; btn.textContent = '書き込み中...'; }}
    fetch('/confirm-weights', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(w)
    }})
    .then(r => r.json())
    .then(data => {{
      if (btn) {{
        btn.disabled = false;
        btn.textContent = data.ok ? ('✅ ' + data.rows + '行更新') : ('❌ ' + (data.error || 'エラー'));
        setTimeout(() => {{ if (btn) btn.textContent = 'このウェイトで確定'; }}, 3000);
      }}
    }})
    .catch(err => {{
      if (btn) {{
        btn.disabled = false;
        btn.textContent = '❌ 通信エラー';
        setTimeout(() => {{ if (btn) btn.textContent = 'このウェイトで確定'; }}, 3000);
      }}
    }});
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

// ---- XMP書き出し ----
window.exportSelections = function() {{
  if (!_SERVER_MODE) return;
  const btn = document.getElementById('btn-export-selections');
  if (btn) {{ btn.disabled = true; btn.textContent = '書き出し中...'; }}
  fetch('/export-selections', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ selections: selectState, excludes: excludeState }})
  }})
  .then(r => r.json())
  .then(data => {{
    if (btn) {{
      btn.disabled = false;
      if (data.ok) {{
        let msg = `✅ ${{data.written}}件書き出し完了`;
        if (data.errors && data.errors.length > 0) {{
          msg += ` (エラー${{data.errors.length}}件)`;
        }}
        btn.textContent = msg;
      }} else {{
        btn.textContent = '❌ ' + (data.error || 'エラー');
      }}
      setTimeout(() => {{ if (btn) btn.textContent = '📤 XMPに書き出す'; }}, 4000);
    }}
  }})
  .catch(() => {{
    if (btn) {{
      btn.disabled = false;
      btn.textContent = '❌ 通信エラー';
      setTimeout(() => {{ if (btn) btn.textContent = '📤 XMPに書き出す'; }}, 4000);
    }}
  }});
}};

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

// ---- 情報ポップアップ ----
(function() {{
  const popup = document.getElementById("info-popup");
  let hideTimer = null;
  window.showInfo = function(e, stem) {{
    e.stopPropagation();
    const card = document.querySelector(".card[data-stem=\"" + stem + "\"]");
    if (!card || !popup) return;
    const sharp   = parseFloat(card.dataset.sharpness);
    const expo    = parseFloat(card.dataset.exposure);
    const eyeRaw  = parseFloat(card.dataset.eye);
    const eyeVal  = eyeRaw >= 0 ? eyeRaw : 0;
    const p       = parseInt(card.dataset.persons);
    const isFirst = card.dataset.position === "first";
    const bonus   = parseFloat(card.dataset.bonus || "1");
    const wSharp = parseFloat((document.getElementById("w-sharpness") || {{}}).value || "0.50");
    const wExpo  = parseFloat((document.getElementById("w-exposure")  || {{}}).value || "0.40");
    const wEye   = parseFloat((document.getElementById("w-eye")       || {{}}).value || "0.20");
    const wPers  = parseFloat((document.getElementById("w-persons")   || {{}}).value || "0.20");
    const wFirst = parseFloat((document.getElementById("w-first")     || {{}}).value || "0.20");
    const total  = wSharp + wExpo + wEye + wPers + wFirst;
    const personNorm = Math.min(p / 3, 1.0);
    const firstVal   = isFirst ? 1.0 : 0.0;
    const raw   = wSharp*sharp + wExpo*expo + wEye*eyeVal + wPers*personNorm + wFirst*firstVal;
    const score = total > 0 ? raw / total : 0;
    function f(v) {{ return v.toFixed(3); }}
    const eyeLabel = eyeRaw < 0 ? "顔なし(0)" : f(eyeVal);
    popup.innerHTML =
      "<div class=\"info-popup-title\">技術スコア " + score.toFixed(3) + "</div>" +
      "<hr class=\"info-popup-divider\">" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">鮮鋭度</span><span class=\"info-popup-val\">" + f(sharp) + " × " + f(wSharp) + "</span></div>" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">露出</span><span class=\"info-popup-val\">" + f(expo) + " × " + f(wExpo) + "</span></div>" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">瞳スコア</span><span class=\"info-popup-val\">" + eyeLabel + " × " + f(wEye) + "</span></div>" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">人物</span><span class=\"info-popup-val\">" + p + "人(" + f(personNorm) + ") × " + f(wPers) + "</span></div>" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">連写位置</span><span class=\"info-popup-val\">" + card.dataset.position + " × " + f(wFirst) + "</span></div>" +
      "<hr class=\"info-popup-divider\">" +
      "<div class=\"info-popup-row\"><span class=\"info-popup-label\">連写ボーナス</span><span class=\"info-popup-val\">×" + f(bonus) + "</span></div>";
    const btn = e.currentTarget || e.target;
    const rect = btn.getBoundingClientRect();
    popup.style.left = Math.min(rect.right + 8, window.innerWidth - 230) + "px";
    popup.style.top  = Math.max(rect.top - 10, 6) + "px";
    popup.classList.add("visible");
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function() {{ popup.classList.remove("visible"); }}, 5000);
  }};
  document.addEventListener("click", function() {{ if (popup) popup.classList.remove("visible"); }});
}})();

// ---- ギャップフラグ ----
(function() {{
  const GAP_KEY = "asa-gaps-" + _FILENAME;
  let gapState = {{}};
  function loadGapState() {{
    try {{ const s = localStorage.getItem(GAP_KEY); gapState = s ? JSON.parse(s) : {{}}; }} catch(e) {{ gapState = {{}}; }}
  }}
  function saveGapState() {{
    try {{ localStorage.setItem(GAP_KEY, JSON.stringify(gapState)); }} catch(e) {{}}
  }}
  window.updateGapVisual = function(stem) {{
    const card = document.querySelector(".card[data-stem=\"" + stem + "\"]");
    if (!card) return;
    card.classList.toggle("gap-flagged", !!gapState[stem]);
    const lbl = document.getElementById("gaplabel-" + stem);
    if (lbl) lbl.style.display = gapState[stem] ? "" : "none";
  }};
  function updateGapSummary() {{
    const el = document.getElementById("gap-count");
    if (el) el.textContent = Object.keys(gapState).length;
  }}
  let _gapStem = null;
  window.openGapModal = function(e, stem) {{
    e.stopPropagation();
    _gapStem = stem;
    const modal = document.getElementById("gap-modal");
    const stemEl = document.getElementById("gap-modal-stem");
    const ta = document.getElementById("gap-modal-memo");
    const rmBtn = document.getElementById("gap-modal-remove");
    if (stemEl) stemEl.textContent = stem;
    const ex = gapState[stem];
    if (ta) ta.value = ex ? (ex.memo || "") : "";
    if (rmBtn) rmBtn.style.display = ex ? "" : "none";
    if (modal) modal.classList.add("open");
    setTimeout(function() {{ if (ta) ta.focus(); }}, 50);
  }};
  window.closeGapModal = function() {{
    const modal = document.getElementById("gap-modal");
    if (modal) modal.classList.remove("open");
    _gapStem = null;
  }};
  window.saveGap = function() {{
    if (!_gapStem) return;
    const ta = document.getElementById("gap-modal-memo");
    gapState[_gapStem] = {{ memo: ta ? ta.value.trim() : "", flaggedAt: new Date().toISOString() }};
    saveGapState(); updateGapVisual(_gapStem); updateGapSummary(); closeGapModal();
  }};
  window.removeGap = function() {{
    if (!_gapStem) return;
    delete gapState[_gapStem];
    saveGapState(); updateGapVisual(_gapStem); updateGapSummary(); closeGapModal();
  }};
  window.exportGaps = function() {{
    if (!_SERVER_MODE) return;
    const btn = document.getElementById("btn-export-gaps");
    if (btn) {{ btn.disabled = true; btn.textContent = "書き出し中..."; }}
    fetch("/export-gaps", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{ gaps: gapState }})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (btn) {{
        btn.disabled = false;
        btn.textContent = d.ok ? ("✅ " + d.count + "件書き出し") : ("❌ " + (d.error || "エラー"));
        setTimeout(function() {{ if (btn) btn.textContent = "📊 ギャップをエクスポート"; }}, 3000);
      }}
    }})
    .catch(function() {{
      if (btn) {{ btn.disabled = false; btn.textContent = "❌ 通信エラー"; setTimeout(function() {{ if (btn) btn.textContent = "📊 ギャップをエクスポート"; }}, 3000); }}
    }});
  }};
  window.shutdownServer = function() {{
    if (!_SERVER_MODE) return;
    if (!confirm("サーバーを停止しますか？")) return;
    fetch("/shutdown", {{ method: "POST" }})
      .then(function() {{ document.body.innerHTML = "<div style=\"color:#ef4444;font-family:sans-serif;padding:40px;font-size:1.2rem;\">サーバーを停止しました</div>"; }})
      .catch(function() {{ document.body.innerHTML = "<div style=\"color:#ef4444;font-family:sans-serif;padding:40px;font-size:1.2rem;\">サーバーを停止しました</div>"; }});
  }};
  document.addEventListener("DOMContentLoaded", function() {{
    const gm = document.getElementById("gap-modal");
    if (gm) gm.addEventListener("click", function(e) {{ if (e.target === this) closeGapModal(); }});
  }});
  loadGapState();
  window._initGapVisuals = function() {{
    Object.keys(gapState).forEach(function(s) {{ updateGapVisual(s); }});
    updateGapSummary();
  }};
}})();

// ---- 初期化 ----
loadSelectState();
loadExcludeState();
loadFilters();
ALL_SHOTS.forEach(s => updateCardVisual(s.stem));
ALL_SHOTS.forEach(s => updateCardExcludeVisual(s.stem));
updateSummary();
applyAllFilters();
if (typeof _initGapVisuals === "function") _initGapVisuals();
</script>
</body>
</html>'''


def generate_client_html(rows: list[dict], jpeg_dir: Path,
                          session_info: dict | None = None,
                          img_base: str = 'file') -> str:
    """クライアント向け 2nd セレクトツール HTML を生成する。

    機能:
      - ナチュラル/オーガニック CSS
      - レーティングフィルタ (=, >, <) / ソート / キーワード検索
      - サムネイルサイズ調整スライダー
      - タグ付け / コレクション
      - クライアントによるレーティング変更
      - 採用フラグ + リアルタイムカウンター
      - localStorage 自動保存
    """
    sorted_rows = sorted(rows, key=lambda r: (r.get('datetime', ''), r['stem']))

    photos = []
    for r in sorted_rows:
        url = find_jpeg(jpeg_dir, r['stem'], img_base)
        photos.append({
            'stem': r['stem'],
            'url':  url,
            'gid':  r['group_id'],
            'dt':   (r.get('datetime') or '')[:19],
            'cr':   r.get('camera_rating', 0),
        })

    photos_json  = json.dumps(photos, ensure_ascii=False)
    n_total      = len(photos)
    title        = html.escape(session_info.get('title', '2nd セレクト') if session_info else '2nd セレクト')
    sess_date    = html.escape(session_info.get('date',  '')              if session_info else '')
    preset_tags  = json.dumps(
        ['お気に入り', 'ヘービー候補', 'SNS向け', 'プリント向け', '要確認', '使わない'],
        ensure_ascii=False,
    )
    sess_date_html = f'<span class="sess-date"> — {sess_date}</span>' if sess_date else ''

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — 2nd セレクト</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "游ゴシック", YuGothic, sans-serif;
  background: #f8f5f0;
  color: #3d3530;
  min-height: 100vh;
}}

/* ---- Header ---- */
.header {{
  position: sticky; top: 0; z-index: 200;
  background: rgba(255,255,255,0.97);
  border-bottom: 1px solid #e5ddd5;
  padding: 14px 20px 10px;
  box-shadow: 0 2px 16px rgba(61,53,48,0.07);
  backdrop-filter: blur(8px);
}}
.header-top {{
  display: flex; align-items: center; gap: 14px;
  margin-bottom: 12px; flex-wrap: wrap;
}}
.site-title {{
  font-size: 1.05rem; font-weight: 700; color: #3d3530;
  flex: 1; letter-spacing: -0.01em;
}}
.site-title .accent {{ color: #7d9b6a; }}
.sess-date {{ font-size: 0.75rem; color: #a09080; font-weight: 400; }}

.adopt-counter {{
  display: flex; align-items: baseline; gap: 5px;
  background: linear-gradient(135deg, #7d9b6a, #6a8a58);
  color: #fff; padding: 6px 16px; border-radius: 24px;
  box-shadow: 0 2px 8px rgba(125,155,106,0.35);
  white-space: nowrap;
}}
.adopt-counter .n {{ font-size: 1.35rem; font-weight: 800; line-height: 1; }}
.adopt-counter .lbl {{ font-size: 0.72rem; opacity: 0.85; }}

/* ---- Controls ---- */
.controls {{
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
}}
.search-wrap {{ position: relative; }}
.search-icon {{
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: #c0b0a0; font-size: 0.8rem; pointer-events: none;
}}
.search-box {{
  padding: 7px 12px 7px 30px;
  border: 1.5px solid #ddd5cc; border-radius: 20px;
  background: #faf8f5; font-size: 0.83rem; color: #3d3530;
  outline: none; width: 185px;
  transition: border-color 0.15s, box-shadow 0.15s;
}}
.search-box:focus {{ border-color: #7d9b6a; box-shadow: 0 0 0 3px rgba(125,155,106,0.12); }}
.search-box::placeholder {{ color: #c0b0a0; }}

.filter-group {{ display: flex; gap: 6px; align-items: center; }}
.filter-label {{ font-size: 0.72rem; color: #a09080; white-space: nowrap; }}

select.filter-select, select.sort-select {{
  padding: 6px 10px; border: 1.5px solid #ddd5cc; border-radius: 8px;
  background: #faf8f5; font-size: 0.8rem; color: #3d3530;
  cursor: pointer; outline: none; transition: border-color 0.15s;
}}
select.filter-select:focus, select.sort-select:focus {{ border-color: #7d9b6a; }}

.size-row {{ display: flex; align-items: center; gap: 6px; }}
.size-row span {{ font-size: 0.72rem; color: #a09080; white-space: nowrap; }}
.size-slider {{
  -webkit-appearance: none; appearance: none;
  width: 80px; height: 4px; border-radius: 2px;
  background: #d8d0c8; outline: none; cursor: pointer;
}}
.size-slider::-webkit-slider-thumb {{
  -webkit-appearance: none; appearance: none;
  width: 14px; height: 14px; border-radius: 50%;
  background: #7d9b6a; cursor: pointer;
  box-shadow: 0 1px 4px rgba(0,0,0,0.2);
}}
.showing-count {{ font-size: 0.77rem; color: #b0a090; white-space: nowrap; margin-left: auto; }}

/* ---- Grid ---- */
.main {{ padding: 20px; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--thumb-size,260px), 1fr));
  gap: 14px;
}}

/* ---- Card ---- */
.card {{
  background: #fff; border: 1.5px solid #ece5dd;
  border-radius: 14px; overflow: hidden;
  transition: box-shadow 0.2s, transform 0.15s, border-color 0.2s;
}}
.card:hover {{ box-shadow: 0 6px 28px rgba(61,53,48,0.10); transform: translateY(-2px); }}
.card.is-adopted {{
  border-color: #7d9b6a;
  box-shadow: 0 0 0 2px rgba(125,155,106,0.18), 0 4px 16px rgba(125,155,106,0.12);
}}
.thumb-wrap {{
  position: relative; aspect-ratio: 3/2;
  overflow: hidden; background: #f0ebe4; cursor: zoom-in;
}}
.thumb-wrap img {{
  width: 100%; height: 100%; object-fit: cover;
  display: block; transition: transform 0.3s;
}}
.card:hover .thumb-wrap img {{ transform: scale(1.025); }}

.adopt-btn {{
  position: absolute; top: 8px; right: 8px;
  width: 34px; height: 34px; border-radius: 50%;
  border: none; cursor: pointer;
  background: rgba(255,255,255,0.88);
  display: flex; align-items: center; justify-content: center;
  font-size: 1rem; font-weight: 700;
  transition: all 0.2s; backdrop-filter: blur(4px);
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
  color: #9d8e85; opacity: 0;
}}
.card:hover .adopt-btn, .card.is-adopted .adopt-btn {{ opacity: 1; }}
.adopt-btn:hover {{ transform: scale(1.12); }}
.card.is-adopted .adopt-btn {{
  background: #7d9b6a; color: #fff;
  box-shadow: 0 2px 8px rgba(125,155,106,0.4);
}}
.gid-badge {{
  position: absolute; bottom: 7px; left: 8px;
  padding: 2px 8px; border-radius: 8px;
  background: rgba(0,0,0,0.38); backdrop-filter: blur(4px);
  font-size: 0.62rem; color: rgba(255,255,255,0.85);
}}

.card-body {{ padding: 10px 12px 12px; }}
.card-name {{
  font-size: 0.7rem; color: #b0a090;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  margin-bottom: 8px;
}}

/* レーティング */
.ratings-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }}
.cam-block, .cli-block {{ display: flex; align-items: center; gap: 4px; }}
.rating-label {{ font-size: 0.62rem; color: #b0a090; white-space: nowrap; }}
.cam-stars {{ color: #c8a45d; font-size: 0.9rem; letter-spacing: 1px; }}
.rating-sep {{ color: #ddd5cc; font-size: 0.6rem; }}
.star-picker {{ display: flex; gap: 1px; }}
.star-pick-btn {{
  background: none; border: none; cursor: pointer;
  font-size: 1.05rem; padding: 0 1px; line-height: 1;
  color: #d8d0c8; transition: color 0.1s, transform 0.1s;
}}
.star-pick-btn:hover {{ transform: scale(1.25); }}
.star-pick-btn.on {{ color: #7d9b6a; }}
.star-reset-btn {{
  background: none; border: none; cursor: pointer;
  font-size: 0.65rem; color: #c0b0a0; padding: 0 2px;
  transition: color 0.1s;
}}
.star-reset-btn:hover {{ color: #9d8e85; }}

/* タグ */
.tags-row {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 7px; min-height: 22px; }}
.tag {{
  display: inline-flex; align-items: center; gap: 3px;
  padding: 2px 8px; border-radius: 10px;
  background: #f0ebe4; border: 1px solid #ddd5c8;
  font-size: 0.67rem; color: #7d6e65; cursor: pointer;
  transition: all 0.15s;
}}
.tag:hover {{ background: #fae8e0; border-color: #d0b8a8; }}
.tag-x {{ font-size: 0.6rem; color: #c0b0a0; }}
.add-tag-btn {{
  padding: 2px 8px; border-radius: 10px;
  background: transparent; border: 1px dashed #c8b8a8;
  font-size: 0.67rem; color: #c0b0a0; cursor: pointer;
  transition: all 0.15s;
}}
.add-tag-btn:hover {{ border-color: #7d9b6a; color: #7d9b6a; background: rgba(125,155,106,0.06); }}

/* メモ */
.note-field {{
  width: 100%; padding: 5px 8px;
  border: 1px solid #ece5dd; border-radius: 7px;
  background: #faf8f5; font-size: 0.72rem;
  font-family: inherit; color: #3d3530;
  resize: none; outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  line-height: 1.45; min-height: 36px;
}}
.note-field:focus {{ border-color: #7d9b6a; box-shadow: 0 0 0 2px rgba(125,155,106,0.10); }}
.note-field::placeholder {{ color: #c8b8a8; font-style: italic; }}

/* ---- 写真モーダル ---- */
.modal {{
  display: none; position: fixed; inset: 0;
  background: rgba(30,22,18,0.90); z-index: 1000;
  align-items: center; justify-content: center;
  flex-direction: column; gap: 14px; cursor: zoom-out;
}}
.modal.open {{ display: flex; }}
.modal-img {{
  max-width: 92vw; max-height: 84vh;
  border-radius: 8px; box-shadow: 0 12px 48px rgba(0,0,0,0.5);
  object-fit: contain;
}}
.modal-footer {{
  display: flex; align-items: center; gap: 16px;
  flex-wrap: wrap; justify-content: center;
}}
.modal-name {{ color: #c8b8a8; font-size: 0.82rem; }}
.modal-close {{
  position: absolute; top: 14px; right: 18px;
  background: none; border: none;
  color: #a09080; font-size: 2rem; cursor: pointer;
  line-height: 1; transition: color 0.15s;
}}
.modal-close:hover {{ color: #f8f5f0; }}
.modal-adopt-btn {{
  padding: 8px 20px; border-radius: 20px;
  border: 2px solid #7d9b6a; background: transparent;
  color: #7d9b6a; font-size: 0.85rem; font-weight: 600; cursor: pointer;
  transition: all 0.2s;
}}
.modal-adopt-btn.on, .modal-adopt-btn:hover {{ background: #7d9b6a; color: #fff; }}

/* ---- タグモーダル ---- */
.tag-modal {{
  display: none; position: fixed; inset: 0;
  background: rgba(30,22,18,0.70); z-index: 1100;
  align-items: center; justify-content: center;
}}
.tag-modal.open {{ display: flex; }}
.tag-modal-box {{
  background: #fff; border-radius: 16px;
  padding: 22px 24px; width: 360px; max-width: 92vw;
  box-shadow: 0 12px 48px rgba(0,0,0,0.2);
}}
.tag-modal-title {{ font-size: 0.9rem; font-weight: 700; color: #3d3530; margin-bottom: 14px; }}
.tag-presets {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }}
.tag-preset-btn {{
  padding: 5px 12px; border-radius: 14px;
  border: 1.5px solid #ddd5cc; background: transparent;
  font-size: 0.78rem; color: #7d6e65; cursor: pointer;
  transition: all 0.15s;
}}
.tag-preset-btn:hover {{ border-color: #7d9b6a; color: #7d9b6a; }}
.tag-preset-btn.on {{ background: #7d9b6a; border-color: #7d9b6a; color: #fff; }}
.tag-custom-row {{ display: flex; gap: 6px; margin-bottom: 14px; }}
.tag-custom-input {{
  flex: 1; padding: 7px 10px;
  border: 1.5px solid #ddd5cc; border-radius: 8px;
  font-size: 0.82rem; color: #3d3530; outline: none;
  transition: border-color 0.15s;
}}
.tag-custom-input:focus {{ border-color: #7d9b6a; }}
.tag-custom-input::placeholder {{ color: #c0b0a0; }}
.tag-add-custom {{
  padding: 7px 14px; border-radius: 8px;
  border: none; background: #7d9b6a; color: #fff;
  font-size: 0.82rem; font-weight: 600; cursor: pointer;
  transition: background 0.15s;
}}
.tag-add-custom:hover {{ background: #6a8a58; }}
.tag-modal-actions {{ display: flex; justify-content: flex-end; }}
.tag-modal-close {{
  padding: 7px 18px; border-radius: 8px;
  border: 1.5px solid #ddd5cc; background: transparent;
  color: #7d6e65; font-size: 0.82rem; cursor: pointer;
}}
.tag-modal-close:hover {{ border-color: #c0b0a0; }}

/* ---- コレクションパネル ---- */
.coll-panel {{
  position: fixed; bottom: 20px; right: 20px;
  background: rgba(255,255,255,0.96); backdrop-filter: blur(8px);
  border: 1.5px solid #e5ddd5; border-radius: 14px;
  padding: 14px 16px; min-width: 180px;
  box-shadow: 0 6px 24px rgba(61,53,48,0.10); z-index: 100;
}}
.coll-title {{ font-size: 0.72rem; font-weight: 700; color: #7d9b6a; margin-bottom: 10px; letter-spacing: 0.04em; text-transform: uppercase; }}
.coll-rows {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; }}
.coll-row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; cursor: pointer; padding: 2px 0; }}
.coll-tag {{ font-size: 0.72rem; color: #7d6e65; }}
.coll-n {{ font-size: 0.78rem; font-weight: 700; color: #3d3530; }}
.coll-row:hover .coll-tag {{ color: #7d9b6a; }}
.export-list-btn {{
  display: block; width: 100%;
  padding: 7px 12px; border-radius: 8px;
  border: 1.5px solid #7d9b6a; background: transparent;
  color: #7d9b6a; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; text-align: center; transition: all 0.15s;
}}
.export-list-btn:hover {{ background: #7d9b6a; color: #fff; }}

@media (max-width: 640px) {{
  .header {{ padding: 12px 14px 10px; }}
  .main {{ padding: 12px; }}
  .grid {{ gap: 10px; }}
  .coll-panel {{ display: none; }}
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <div class="site-title">
      {title}{sess_date_html}
    </div>
    <div class="adopt-counter">
      <span class="n" id="adopt-count">0</span>
      <span class="lbl">枚採用</span>
    </div>
  </div>
  <div class="controls">
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input class="search-box" id="search-box" type="search"
             placeholder="ファイル名 / タグ / メモ…"
             oninput="onSearch(this.value)">
    </div>
    <div class="filter-group">
      <span class="filter-label">絞り込み</span>
      <select class="filter-select" id="filter-op" onchange="onFilterChange()">
        <option value="all">すべて ({n_total}枚)</option>
        <option value="eq3">★★★ のみ</option>
        <option value="eq2">★★ のみ</option>
        <option value="eq1">★ のみ</option>
        <option value="gte2">★★ 以上</option>
        <option value="adopted">採用済みのみ</option>
      </select>
    </div>
    <div class="filter-group">
      <span class="filter-label">並び替え</span>
      <select class="sort-select" id="sort-select" onchange="onSortChange()">
        <option value="dt_asc">撮影時刻 ↑</option>
        <option value="dt_desc">撮影時刻 ↓</option>
        <option value="cr_desc">撮影者レーティング ↓</option>
        <option value="cr_asc">撮影者レーティング ↑</option>
        <option value="cl_desc">あなたのレーティング ↓</option>
        <option value="cl_asc">あなたのレーティング ↑</option>
      </select>
    </div>
    <div class="size-row">
      <span>🖼 サイズ</span>
      <input class="size-slider" id="size-slider" type="range"
             min="160" max="420" step="20" value="260"
             oninput="onSizeChange(this.value)">
    </div>
    <span class="showing-count" id="showing-count">{n_total} 枚</span>
  </div>
</div>

<div class="main">
  <div class="grid" id="grid"></div>
</div>

<!-- 写真モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img class="modal-img" id="modal-img" src="" alt="">
  <div class="modal-footer">
    <span class="modal-name" id="modal-name"></span>
    <button class="modal-adopt-btn" id="modal-adopt-btn" onclick="toggleAdoptModal()">採用する</button>
  </div>
</div>

<!-- タグモーダル -->
<div class="tag-modal" id="tag-modal" onclick="closeTagModalOverlay(event)">
  <div class="tag-modal-box">
    <div class="tag-modal-title" id="tag-modal-title">タグを編集</div>
    <div class="tag-presets" id="tag-presets"></div>
    <div class="tag-custom-row">
      <input class="tag-custom-input" id="tag-custom-input"
             placeholder="カスタムタグを追加…"
             onkeydown="if(event.key==='Enter')addCustomTag()">
      <button class="tag-add-custom" onclick="addCustomTag()">追加</button>
    </div>
    <div class="tag-modal-actions">
      <button class="tag-modal-close" onclick="closeTagModal()">閉じる</button>
    </div>
  </div>
</div>

<!-- コレクションパネル -->
<div class="coll-panel" id="coll-panel">
  <div class="coll-title">コレクション</div>
  <div class="coll-rows" id="coll-rows">
    <div style="font-size:0.72rem;color:#c0b0a0">タグなし</div>
  </div>
  <button class="export-list-btn" onclick="exportList()">採用リストを出力</button>
</div>

<script>
/* ---- Data ---- */
const PHOTOS = {photos_json};
const PRESET_TAGS = {preset_tags};
const _FILE = window.location.pathname.split('/').pop() || 'client.html';
const STORE = 'asa-client2-' + _FILE;

/* ---- State ---- */
let S = {{
  ratings: {{}},
  adopted: {{}},
  tags: {{}},
  notes: {{}},
  ui: {{ filter_op: 'all', sort: 'dt_asc', thumb_size: 260, search: '' }}
}};

function save() {{ try {{ localStorage.setItem(STORE, JSON.stringify(S)); }} catch(e) {{}} }}
function load() {{
  try {{
    const raw = localStorage.getItem(STORE);
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.ratings) S.ratings = d.ratings;
    if (d.adopted)  S.adopted  = d.adopted;
    if (d.tags)     S.tags     = d.tags;
    if (d.notes)    S.notes    = d.notes;
    if (d.ui)       Object.assign(S.ui, d.ui);
  }} catch(e) {{}}
}}

/* ---- 有効レーティング (クライアント設定 > 撮影者) ---- */
function effRating(stem) {{
  const cr = S.ratings[stem];
  if (cr !== undefined && cr !== null) return cr;
  const p = PHOTOS.find(x => x.stem === stem);
  return p ? (p.cr || 0) : 0;
}}

/* ---- フィルタ・ソート ---- */
function filterAndSort() {{
  const op = S.ui.filter_op;
  const q  = S.ui.search.toLowerCase().trim();

  let list = PHOTOS.filter(p => {{
    const r = effRating(p.stem);
    if (op === 'eq1' && r !== 1) return false;
    if (op === 'eq2' && r !== 2) return false;
    if (op === 'eq3' && r !== 3) return false;
    if (op === 'gte2' && r < 2)  return false;
    if (op === 'adopted' && !S.adopted[p.stem]) return false;
    if (q) {{
      const inStem = p.stem.toLowerCase().includes(q);
      const inTags = (S.tags[p.stem] || []).some(t => t.toLowerCase().includes(q));
      const inNote = (S.notes[p.stem] || '').toLowerCase().includes(q);
      if (!inStem && !inTags && !inNote) return false;
    }}
    return true;
  }});

  const sort = S.ui.sort;
  list.sort((a, b) => {{
    if (sort === 'dt_asc')  return a.dt < b.dt ? -1 : a.dt > b.dt ? 1 : 0;
    if (sort === 'dt_desc') return b.dt < a.dt ? -1 : b.dt > a.dt ? 1 : 0;
    if (sort === 'cr_asc')  return a.cr !== b.cr ? a.cr - b.cr : (a.dt < b.dt ? -1 : 1);
    if (sort === 'cr_desc') return a.cr !== b.cr ? b.cr - a.cr : (a.dt < b.dt ? -1 : 1);
    const ca = effRating(a.stem), cb = effRating(b.stem);
    if (sort === 'cl_asc')  return ca !== cb ? ca - cb : (a.dt < b.dt ? -1 : 1);
    if (sort === 'cl_desc') return ca !== cb ? cb - ca : (a.dt < b.dt ? -1 : 1);
    return 0;
  }});
  return list;
}}

/* ---- カードレンダリング ---- */
function renderCard(p) {{
  const er   = effRating(p.stem);
  const isAd = !!S.adopted[p.stem];
  const tags = S.tags[p.stem] || [];
  const note = (S.notes[p.stem] || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
  const se   = p.stem.replace(/\\/g,'\\\\').replace(/'/g,"\\'");

  const camH = p.cr > 0
    ? '<span class="cam-stars">' + '★'.repeat(p.cr) + '</span>'
    : '<span class="cam-stars" style="color:#d8d0c8">—</span>';

  let pickH = '';
  for (let i = 1; i <= 3; i++) {{
    pickH += `<button class="star-pick-btn ${{er >= i ? 'on' : ''}}" onclick="setRating('${{se}}',${{i}})" title="${{i}}★">★</button>`;
  }}
  if (er > 0) pickH += `<button class="star-reset-btn" onclick="setRating('${{se}}',0)" title="リセット">✕</button>`;

  const tagsH = tags.map(t => {{
    const te = t.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/</g,'&lt;');
    return `<span class="tag" onclick="removeTag('${{se}}','${{te}}')">${{t}} <span class="tag-x">×</span></span>`;
  }}).join('') + `<button class="add-tag-btn" onclick="openTagModal('${{se}}')">+ タグ</button>`;

  const dt = p.dt ? p.dt.slice(11, 16) : '';

  return `<div class="card ${{isAd ? 'is-adopted' : ''}}" id="card-${{p.stem}}">
  <div class="thumb-wrap" onclick="openModal('${{p.url}}','${{se}}')">
    <img src="${{p.url}}" alt="${{p.stem}}" loading="lazy">
    <button class="adopt-btn" onclick="toggleAdopt(event,'${{se}}')" title="${{isAd ? '採用解除' : '採用する'}}">${{isAd ? '✓' : '○'}}</button>
    <div class="gid-badge">G${{p.gid}} ${{dt}}</div>
  </div>
  <div class="card-body">
    <div class="card-name">${{p.stem}}</div>
    <div class="ratings-row">
      <div class="cam-block"><span class="rating-label">撮影者</span>${{camH}}</div>
      <span class="rating-sep">◆</span>
      <div class="cli-block">
        <span class="rating-label" style="color:#7d9b6a">あなた</span>
        <div class="star-picker">${{pickH}}</div>
      </div>
    </div>
    <div class="tags-row" id="tags-${{p.stem}}">${{tagsH}}</div>
    <textarea class="note-field" placeholder="メモ…" rows="1"
      onchange="setNote('${{se}}',this.value)"
      onfocus="this.rows=3" onblur="if(!this.value.trim())this.rows=1"
    >${{note}}</textarea>
  </div>
</div>`;
}}

function renderGrid() {{
  const filtered = filterAndSort();
  document.getElementById('grid').innerHTML = filtered.map(renderCard).join('');
  document.getElementById('showing-count').textContent = filtered.length + ' / ' + PHOTOS.length + ' 枚';
  updateAdoptCounter();
  updateCollPanel();
}}

/* ---- 採用 ---- */
window.toggleAdopt = function(e, stem) {{
  if (e) e.stopPropagation();
  S.adopted[stem] = !S.adopted[stem];
  save(); updateCard(stem); updateAdoptCounter(); updateCollPanel();
}};
function updateAdoptCounter() {{
  document.getElementById('adopt-count').textContent =
    Object.values(S.adopted).filter(Boolean).length;
}}

/* ---- レーティング ---- */
window.setRating = function(stem, val) {{
  if (val === 0 || S.ratings[stem] === val) delete S.ratings[stem];
  else S.ratings[stem] = val;
  save(); updateCard(stem);
  if (S.ui.filter_op !== 'all') renderGrid();
}};

/* ---- タグ ---- */
let _tagStem = null;
window.openTagModal = function(stem) {{
  _tagStem = stem;
  const cur = S.tags[stem] || [];
  document.getElementById('tag-modal-title').textContent = stem + ' — タグ編集';
  document.getElementById('tag-presets').innerHTML = PRESET_TAGS.map(t => {{
    const on = cur.includes(t);
    const te = t.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    return `<button class="tag-preset-btn ${{on ? 'on' : ''}}" onclick="togglePresetTag('${{te}}')">${{on ? '✓ ' : ''}}${{t}}</button>`;
  }}).join('');
  document.getElementById('tag-custom-input').value = '';
  document.getElementById('tag-modal').classList.add('open');
}};
window.closeTagModal = function() {{
  document.getElementById('tag-modal').classList.remove('open');
  _tagStem = null;
}};
window.closeTagModalOverlay = function(e) {{
  if (e.target === document.getElementById('tag-modal')) window.closeTagModal();
}};
window.togglePresetTag = function(tag) {{
  if (!_tagStem) return;
  const cur = S.tags[_tagStem] || [];
  const idx = cur.indexOf(tag);
  if (idx >= 0) cur.splice(idx, 1); else cur.push(tag);
  S.tags[_tagStem] = cur;
  save(); updateCard(_tagStem); window.openTagModal(_tagStem);
}};
window.addCustomTag = function() {{
  if (!_tagStem) return;
  const input = document.getElementById('tag-custom-input');
  const tag = input.value.trim();
  if (!tag) return;
  const cur = S.tags[_tagStem] || [];
  if (!cur.includes(tag)) {{ cur.push(tag); S.tags[_tagStem] = cur; save(); updateCard(_tagStem); }}
  input.value = '';
  window.openTagModal(_tagStem);
}};
window.removeTag = function(stem, tag) {{
  const cur = S.tags[stem] || [];
  const idx = cur.indexOf(tag);
  if (idx >= 0) {{ cur.splice(idx, 1); save(); updateCard(stem); updateCollPanel(); }}
}};

/* ---- メモ ---- */
window.setNote = function(stem, val) {{
  if (val.trim()) S.notes[stem] = val; else delete S.notes[stem];
  save();
}};

/* ---- カード個別更新 ---- */
function updateCard(stem) {{
  const el = document.getElementById('card-' + stem);
  if (!el) return;
  const p = PHOTOS.find(x => x.stem === stem);
  if (p) el.outerHTML = renderCard(p);
}}

/* ---- UI制御 ---- */
window.onSearch = function(val) {{ S.ui.search = val; save(); renderGrid(); }};
window.onFilterChange = function() {{
  S.ui.filter_op = document.getElementById('filter-op').value;
  save(); renderGrid();
}};
window.onSortChange = function() {{
  S.ui.sort = document.getElementById('sort-select').value;
  save(); renderGrid();
}};
window.onSizeChange = function(val) {{
  S.ui.thumb_size = parseInt(val, 10);
  document.documentElement.style.setProperty('--thumb-size', val + 'px');
  save();
}};

/* ---- 写真モーダル ---- */
let _modalStem = null;
window.openModal = function(url, stem) {{
  _modalStem = stem;
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = stem;
  const btn = document.getElementById('modal-adopt-btn');
  const isAd = !!S.adopted[stem];
  btn.textContent = isAd ? '採用解除' : '採用する';
  btn.classList.toggle('on', isAd);
  document.getElementById('modal').classList.add('open');
}};
window.closeModal = function(e) {{
  if (e && !e.target.classList.contains('modal') && !e.target.classList.contains('modal-close')) return;
  document.getElementById('modal').classList.remove('open');
  document.getElementById('modal-img').src = '';
  _modalStem = null;
}};
window.toggleAdoptModal = function() {{
  if (!_modalStem) return;
  window.toggleAdopt(null, _modalStem);
  const btn = document.getElementById('modal-adopt-btn');
  const isAd = !!S.adopted[_modalStem];
  btn.textContent = isAd ? '採用解除' : '採用する';
  btn.classList.toggle('on', isAd);
}};

/* ---- コレクションパネル ---- */
function updateCollPanel() {{
  const tagCounts = {{}};
  Object.values(S.tags).forEach(tags => tags.forEach(t => {{
    tagCounts[t] = (tagCounts[t] || 0) + 1;
  }}));
  const el = document.getElementById('coll-rows');
  if (Object.keys(tagCounts).length === 0) {{
    el.innerHTML = '<div style="font-size:0.72rem;color:#c0b0a0">タグなし</div>';
    return;
  }}
  el.innerHTML = Object.entries(tagCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => {{
      const te = t.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      return `<div class="coll-row" onclick="filterByTag('${{te}}')"><span class="coll-tag">${{t}}</span><span class="coll-n">${{n}}</span></div>`;
    }}).join('');
}}
window.filterByTag = function(tag) {{
  S.ui.search = tag;
  document.getElementById('search-box').value = tag;
  save(); renderGrid();
}};

/* ---- 採用リスト出力 ---- */
window.exportList = function() {{
  const adopted = PHOTOS.filter(p => S.adopted[p.stem]);
  if (!adopted.length) {{ alert('採用フラグが付いた写真がありません'); return; }}
  const lines = ['採用リスト', '', '採用数: ' + adopted.length + ' 枚', ''];
  adopted.forEach(p => {{
    lines.push(p.stem);
    const r = effRating(p.stem);
    if (r > 0) lines.push('  レーティング: ' + r + '★');
    const tags = (S.tags[p.stem] || []).join(', ');
    if (tags) lines.push('  タグ: ' + tags);
    const note = S.notes[p.stem] || '';
    if (note) lines.push('  メモ: ' + note);
    lines.push('');
  }});
  const blob = new Blob([lines.join('\\n')], {{type: 'text/plain;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'adopted_list.txt';
  a.click();
}};

/* ---- キーボード ---- */
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') {{ window.closeModal(); window.closeTagModal(); }}
}});

/* ---- 初期化 ---- */
load();
document.getElementById('filter-op').value  = S.ui.filter_op  || 'all';
document.getElementById('sort-select').value = S.ui.sort       || 'dt_asc';
document.getElementById('size-slider').value = S.ui.thumb_size || 260;
document.getElementById('search-box').value  = S.ui.search     || '';
document.documentElement.style.setProperty('--thumb-size', (S.ui.thumb_size || 260) + 'px');
renderGrid();
</script>
</body>
</html>'''


# ---------- Flaskサーバー ----------

def _recalc_technical_score(row: dict, w: dict) -> float:
    sharp      = float(row.get('sharpness_score', 0) or 0)
    expo       = float(row.get('exposure_score',  0) or 0)
    eye_raw    = float(row.get('eye_score',       -1) or -1)
    eye_val    = max(0.0, eye_raw)
    n_persons  = int(float(row.get('person_count', 0) or 0))
    is_first   = row.get('position', '') == 'first'
    total = w['sharpness'] + w['exposure'] + w['eye'] + w['persons'] + w['first']
    raw = (w['sharpness'] * sharp
         + w['exposure']  * expo
         + w['eye']       * eye_val
         + w['persons']   * min(n_persons / 3.0, 1.0)
         + w['first']     * (1.0 if is_first else 0.0))
    return raw / total if total > 0 else 0.0


def _find_image(jpeg_dir: Path, stem: str) -> Path | None:
    """stem (拡張子なしファイル名) から実際の画像パスを返す。見つからなければ None。"""
    for ext in ('.JPG', '.jpg', '.jpeg', '.JPEG', '.CR3', '.cr3', '.ARW', '.arw',
                '.NEF', '.nef', '.RAF', '.raf'):
        p = jpeg_dir / (stem + ext)
        if p.exists():
            return p
    return None


def start_flask_server(jpeg_dir: Path, csv_path: Path,
                       session_info: dict | None, port: int = 5002) -> None:
    try:
        from flask import Flask, request, jsonify, send_from_directory
    except ImportError:
        print('エラー: flask がインストールされていません。')
        print('  pip install flask')
        sys.exit(1)

    # xmp_writer を stage6/ からインポート
    _stage6_dir = str(Path(__file__).parent.parent / 'stage6')
    if _stage6_dir not in sys.path:
        sys.path.insert(0, _stage6_dir)
    try:
        from xmp_writer import update_metadata as _update_metadata
    except ImportError:
        _update_metadata = None

    # セッション情報をファイルに保存（--resume / --stop 用）
    import json as _json
    import datetime as _dt
    _session_file = csv_path.parent / 'report_session.json'
    _session_data = {
        'jpeg_dir': str(jpeg_dir),
        'csv_path': str(csv_path),
        'session_json': str(Path(session_info.get('_path', ''))) if session_info and session_info.get('_path') else None,
        'port': port,
        'pid': __import__('os').getpid(),
        'started_at': _dt.datetime.now().isoformat(),
    }
    with open(_session_file, 'w', encoding='utf-8') as _sf:
        _json.dump(_session_data, _sf, ensure_ascii=False, indent=2)
    print(f'セッション情報を保存: {_session_file}')

    app = Flask(__name__)
    html_holder: dict = {'html': ''}

    def _refresh_html() -> None:
        rows = load_groups(csv_path)
        html_holder['html'] = generate_html(
            rows, jpeg_dir, session_info,
            img_base='/img', server_mode=True,
        )

    _refresh_html()

    @app.route('/')
    def index():
        return html_holder['html'], 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/img/<path:filename>')
    def serve_image(filename):
        return send_from_directory(str(jpeg_dir), filename)

    @app.route('/confirm-weights', methods=['POST'])
    def confirm_weights():
        try:
            w = request.get_json(force=True)
            required = {'sharpness', 'exposure', 'eye', 'persons', 'first'}
            if not required.issubset(w.keys()):
                return jsonify({'ok': False, 'error': 'missing weights'}), 400

            with open(csv_path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                all_rows = list(reader)

            for row in all_rows:
                row['technical_score'] = f'{_recalc_technical_score(row, w):.4f}'

            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_rows)

            _refresh_html()
            return jsonify({'ok': True, 'rows': len(all_rows)})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/export-selections', methods=['POST'])
    def export_selections():
        if _update_metadata is None:
            return jsonify({'ok': False, 'error': 'xmp_writer が見つかりません (stage6/xmp_writer.py)'}), 500
        try:
            data = request.get_json(force=True)
            selections = data.get('selections', {})  # stem -> 'good'|'fine'|'keep'
            excludes   = data.get('excludes',   {})  # stem -> true

            RATING_MAP = {'good': 3, 'fine': 2, 'keep': 1}

            written = 0
            errors: list[str] = []

            for stem, grade in selections.items():
                star = RATING_MAP.get(grade, 1)
                img = _find_image(jpeg_dir, stem)
                if img is None:
                    errors.append(f'{stem} (not found)')
                    continue
                result = _update_metadata(img, star)
                if result == 'error':
                    errors.append(stem)
                else:
                    written += 1

            for stem in excludes:
                if excludes[stem]:
                    img = _find_image(jpeg_dir, stem)
                    if img is None:
                        errors.append(f'{stem} (not found)')
                        continue
                    result = _update_metadata(img, -1)
                    if result == 'error':
                        errors.append(stem)
                    else:
                        written += 1

            return jsonify({'ok': True, 'written': written, 'errors': errors})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/shutdown', methods=['POST'])
    def shutdown():
        import threading as _threading
        _threading.Timer(0.3, lambda: __import__('os')._exit(0)).start()
        return jsonify({'ok': True})

    @app.route('/export-gaps', methods=['POST'])
    def export_gaps():
        try:
            data = request.get_json(force=True)
            gaps = data.get('gaps', {})
            import json as _json2
            import datetime as _dt2
            output_path = csv_path.parent / 'stage2_gaps.json'
            with open(output_path, 'w', encoding='utf-8') as _f:
                _json2.dump({
                    'exported_at': _dt2.datetime.now().isoformat(),
                    'total': len(gaps),
                    'gaps': [
                        {
                            'stem': stem,
                            'memo': info.get('memo', '') if isinstance(info, dict) else str(info),
                            'flaggedAt': info.get('flaggedAt', '') if isinstance(info, dict) else ''
                        }
                        for stem, info in gaps.items()
                    ]
                }, _f, ensure_ascii=False, indent=2)
            return jsonify({'ok': True, 'count': len(gaps), 'path': str(output_path)})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    import webbrowser
    url = f'http://localhost:{port}/'
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f'\nFlaskサーバー起動: {url}')
    print('終了するには Ctrl+C を押してください\n')
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


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
    parser.add_argument('--serve', action='store_true',
                        help='Flaskサーバーモードで起動（ウェイト確定ボタン有効）')
    parser.add_argument('--daemon', action='store_true',
                        help='バックグラウンドデーモンとして起動（Claudeセッション終了後も継続）')
    parser.add_argument('--resume', metavar='SESSION_FILE', default=None,
                        help='保存されたセッションファイルからサーバーを再起動')
    parser.add_argument('--stop', metavar='SESSION_FILE', default=None,
                        help='セッションファイルのPIDを読んでサーバーを停止')
    parser.add_argument('--port', type=int, default=5002,
                        help='Flaskサーバーポート番号 (デフォルト: 5002)')
    parser.add_argument('--client', action='store_true',
                        help='クライアント向け 2nd セレクトツールを生成する')
    args = parser.parse_args()

    # --stop
    if args.stop:
        import json as _json, signal as _signal
        _stop_path = Path(args.stop)
        if not _stop_path.exists():
            print(f'エラー: セッションファイルが見つかりません: {_stop_path}')
            sys.exit(1)
        with open(_stop_path, encoding='utf-8') as _f:
            _sess = _json.load(_f)
        _pid = _sess.get('pid')
        if not _pid:
            print('エラー: セッションファイルに PID がありません')
            sys.exit(1)
        try:
            import os as _os
            _os.kill(_pid, _signal.SIGTERM)
            print(f'サーバーを停止しました (PID: {_pid})')
            with open(_stop_path, 'w', encoding='utf-8') as _f:
                import json as _json2
                _json2.dump({'stopped': True, 'pid': _pid}, _f)
        except ProcessLookupError:
            print(f'プロセスが見つかりません (PID: {_pid}) — すでに停止済みかもしれません')
        sys.exit(0)

    # --resume
    if args.resume:
        import json as _json
        _resume_path = Path(args.resume)
        if not _resume_path.exists():
            print(f'エラー: セッションファイルが見つかりません: {_resume_path}')
            sys.exit(1)
        with open(_resume_path, encoding='utf-8') as _f:
            _sess = _json.load(_f)
        _jpeg_dir_r = Path(_sess['jpeg_dir'])
        _csv_path_r = Path(_sess['csv_path'])
        _port_r     = _sess.get('port', 5002)
        _session_info_r = None
        _sj = _sess.get('session_json')
        if _sj and Path(_sj).exists():
            with open(Path(_sj), encoding='utf-8') as _f2:
                _session_info_r = _json.load(_f2)
            _session_info_r['_path'] = str(Path(_sj))
        print(f'セッションを再開: {_resume_path}')
        start_flask_server(_jpeg_dir_r, _csv_path_r, _session_info_r, port=_port_r)
        sys.exit(0)

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
        session_info['_path'] = str(session_json_path)

    if args.serve:
        if args.daemon:
            import subprocess as _sp
            import time as _time
            _new_argv = [a for a in sys.argv[1:] if a != '--daemon']
            _log_path = csv_path.parent / 'report_server.log'
            _proc = _sp.Popen(
                [sys.executable, str(Path(__file__).resolve())] + _new_argv,
                stdout=open(_log_path, 'w'),
                stderr=_sp.STDOUT,
                start_new_session=True,
            )
            _time.sleep(0.8)
            _url = f'http://localhost:{args.port}/'
            _session_file = csv_path.parent / 'report_session.json'
            _resume_cmd = f'python {Path(__file__).resolve()} --resume {_session_file}'
            _stop_cmd   = f'python {Path(__file__).resolve()} --stop {_session_file}'
            print(f'\nデーモンとして起動しました (PID: {_proc.pid})')
            print(f'URL:      {_url}')
            print(f'再起動:   {_resume_cmd}')
            print(f'停止:     {_stop_cmd}')
            print(f'ログ:     {_log_path}')
            sys.exit(0)
        start_flask_server(jpeg_dir, csv_path, session_info, port=args.port)
        sys.exit(0)

    rows = load_groups(csv_path)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = csv_path.parent / output_path

    if args.client:
        client_output = output_path.parent / (output_path.stem + '_client' + output_path.suffix) \
            if args.output != 'stage2_report.html' else csv_path.parent / 'stage2_client.html'
        html_str = generate_client_html(rows, jpeg_dir, session_info)
        client_output.write_text(html_str, encoding='utf-8')
        n_groups = max(r['group_id'] for r in rows) + 1
        print(f'クライアントレポート生成完了: {client_output}')
        print(f'グループ数: {n_groups} / ショット数: {len(rows)}')
        print(f'\n  open "{client_output}"')
        sys.exit(0)

    html_str = generate_html(rows, jpeg_dir, session_info)
    output_path.write_text(html_str, encoding='utf-8')

    n_groups = max(r['group_id'] for r in rows) + 1
    print(f'レポート生成完了: {output_path}')
    print(f'グループ数: {n_groups} / ショット数: {len(rows)}')
    print(f'\n  open "{output_path}"')


if __name__ == '__main__':
    main()
