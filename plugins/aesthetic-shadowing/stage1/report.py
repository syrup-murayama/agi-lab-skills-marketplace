#!/usr/bin/env python3
"""
Stage 1 HTMLレポート生成ツール

Stage1 CSVとLightroom XMPを照合し、
サムネイル付きの比較レポートをHTMLで出力する。

使い方:
  python report.py <jpeg_dir> <xmp_dir> <stage1_csv> [--output report.html]

例:
  python report.py \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/S2_JPEG/ \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/xmp_output/ \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/xmp_output/stage1_results.csv
"""

import argparse
import csv
import html
import re
import sys
from pathlib import Path


# ---------- データ読み込み ----------

def parse_xmp_verdict(xmp_path: Path) -> dict:
    text = xmp_path.read_text(encoding='utf-8')
    pick_match = re.search(r'xmpDM:pick="(-?\d+)"', text)
    rating_match = re.search(r'xmp:Rating="(-?\d+)"', text)
    pick = int(pick_match.group(1)) if pick_match else 0
    rating = int(rating_match.group(1)) if rating_match else 0
    return {
        'stem': xmp_path.stem,
        'pick': pick,
        'rating': rating,
        'human_rejected': pick == -1,
    }


def load_xmp_dir(xmp_dir: Path) -> dict[str, dict]:
    results = {}
    for xmp_path in sorted(xmp_dir.glob('*.xmp')):
        v = parse_xmp_verdict(xmp_path)
        results[v['stem']] = v
    return results


def load_stage1_csv(csv_path: Path) -> dict[str, dict]:
    results = {}
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            stem = Path(row['file']).stem
            results[stem] = {
                'stem': stem,
                'blur_score': float(row['blur_score']),
                'blown_pct': float(row['blown_pct']),
                'dark_pct': float(row['dark_pct']),
                'flags': row['flags'],
                'stage1_rejected': row['rejected'].strip().lower() == 'true',
            }
    return results


def find_jpeg(jpeg_dir: Path, stem: str) -> Path | None:
    for ext in ('.JPG', '.jpg', '.jpeg', '.JPEG'):
        p = jpeg_dir / (stem + ext)
        if p.exists():
            return p
    return None


# ---------- HTML生成 ----------

LABEL = {
    'TP': ('TP', '#22c55e', '両者が却下（正解）'),
    'TN': ('TN', '#94a3b8', '両者がOK（正解）'),
    'FP': ('FP 誤検出', '#f97316', '人間はOK、Stage1が却下'),
    'FN': ('FN 見逃し', '#ef4444', '人間が却下、Stage1はOK'),
}

STARS = {0: '', 1: '★', 2: '★★', 3: '★★★', 4: '★★★★', 5: '★★★★★'}


def make_card(row: dict) -> str:
    result = row['result']
    label_text, color, tooltip = LABEL[result]
    jpeg_url = row['jpeg_url']
    stem = html.escape(row['stem'])
    flags = html.escape(row['flags'])
    stars = STARS.get(row['rating'], '')
    pick_text = '🚫 却下' if row['human_rejected'] else ('OK' if not stars else '')

    stage1_verdict = '🚫 NG' if row['stage1_rejected'] else '✓ OK'
    stage1_color = '#ef4444' if row['stage1_rejected'] else '#22c55e'
    human_color = '#ef4444' if row['human_rejected'] else '#22c55e'

    blur = row['blur_score']
    blown = row['blown_pct']
    dark = row['dark_pct']

    # スコアバーの幅（視覚化用、最大値を固定）
    blur_bar = min(blur / 1200 * 100, 100)
    blown_bar = min(blown, 100)
    dark_bar = min(dark, 100)

    return f'''
<div class="card" data-result="{result.lower().split()[0]}"
     data-blur="{blur}" data-blown="{blown}">
  <div class="thumb-wrap" onclick="openModal('{jpeg_url}', '{stem}')">
    <img src="{jpeg_url}" alt="{stem}" loading="lazy">
    <div class="badge" style="background:{color}" title="{tooltip}">{label_text}</div>
  </div>
  <div class="info">
    <div class="filename">{stem}</div>
    <div class="verdicts">
      <span class="verdict" style="color:{human_color}">
        人間: {pick_text}{stars if stars else ('OK' if not row['human_rejected'] else '')}
      </span>
      <span class="verdict" style="color:{stage1_color}">
        Stage1: {stage1_verdict}
      </span>
    </div>
    {f'<div class="flags">{flags}</div>' if flags != 'OK' else ''}
    <div class="metrics">
      <div class="metric-row">
        <span class="metric-label">ピンボケ</span>
        <div class="bar-bg"><div class="bar" style="width:{blur_bar:.1f}%;background:#6366f1"></div></div>
        <span class="metric-val">{blur:.0f}</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">白飛び</span>
        <div class="bar-bg"><div class="bar" style="width:{blown_bar:.1f}%;background:#f59e0b"></div></div>
        <span class="metric-val">{blown:.1f}%</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">黒潰れ</span>
        <div class="bar-bg"><div class="bar" style="width:{dark_bar:.1f}%;background:#64748b"></div></div>
        <span class="metric-val">{dark:.1f}%</span>
      </div>
    </div>
  </div>
</div>'''


def generate_html(rows: list[dict], stats: dict, thresholds: dict) -> str:
    cards_html = '\n'.join(make_card(r) for r in rows)

    total = stats['total']
    tp = stats['tp']
    fp = stats['fp']
    fn = stats['fn']
    tn = stats['tn']
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    blur_th = thresholds['blur']
    blown_th = thresholds['blown']
    dark_th = thresholds['dark']

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stage 1 評価レポート</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
  }}

  /* ヘッダー */
  .header {{
    background: #1e293b;
    border-bottom: 1px solid #334155;
    padding: 20px 24px;
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header h1 {{ font-size: 1.2rem; color: #f1f5f9; margin-bottom: 16px; }}
  .header h1 span {{ color: #818cf8; }}

  /* 統計サマリー */
  .stats {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }}
  .stat-box {{
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 14px;
    text-align: center;
    min-width: 90px;
  }}
  .stat-box .num {{ font-size: 1.4rem; font-weight: 700; }}
  .stat-box .lbl {{ font-size: 0.7rem; color: #94a3b8; margin-top: 2px; }}
  .stat-tp .num {{ color: #22c55e; }}
  .stat-tn .num {{ color: #94a3b8; }}
  .stat-fp .num {{ color: #f97316; }}
  .stat-fn .num {{ color: #ef4444; }}
  .stat-f1 .num {{ color: #818cf8; }}

  /* 閾値表示 */
  .thresholds {{
    display: flex;
    gap: 16px;
    font-size: 0.75rem;
    color: #64748b;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }}
  .thresholds span {{ color: #94a3b8; }}

  /* フィルターバー */
  .filters {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .filter-btn {{
    padding: 5px 14px;
    border-radius: 20px;
    border: 1px solid #334155;
    background: transparent;
    color: #94a3b8;
    cursor: pointer;
    font-size: 0.8rem;
    transition: all 0.15s;
  }}
  .filter-btn:hover {{ border-color: #818cf8; color: #818cf8; }}
  .filter-btn.active {{ background: #818cf8; border-color: #818cf8; color: #fff; }}
  .filter-btn.fp-btn.active {{ background: #f97316; border-color: #f97316; }}
  .filter-btn.fn-btn.active {{ background: #ef4444; border-color: #ef4444; }}
  .filter-btn.tp-btn.active {{ background: #22c55e; border-color: #22c55e; }}

  /* グリッド */
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
    padding: 20px 24px;
  }}

  /* カード */
  .card {{
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    overflow: hidden;
    transition: transform 0.15s, border-color 0.15s;
  }}
  .card:hover {{ transform: translateY(-2px); border-color: #818cf8; }}
  .card[data-result="fp"] {{ border-color: #7c2d12; }}
  .card[data-result="fn"] {{ border-color: #7f1d1d; }}
  .card[data-result="tp"] {{ border-color: #14532d; }}

  .thumb-wrap {{
    position: relative;
    aspect-ratio: 3/2;
    background: #0f172a;
    cursor: zoom-in;
    overflow: hidden;
  }}
  .thumb-wrap img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform 0.2s;
  }}
  .thumb-wrap:hover img {{ transform: scale(1.03); }}
  .thumb-wrap .no-img {{
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: #475569;
    font-size: 0.75rem;
  }}

  .badge {{
    position: absolute;
    top: 8px;
    right: 8px;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 700;
    color: #fff;
    box-shadow: 0 2px 4px rgba(0,0,0,0.4);
  }}

  .info {{
    padding: 10px 12px;
  }}
  .filename {{
    font-size: 0.75rem;
    color: #94a3b8;
    margin-bottom: 6px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .verdicts {{
    display: flex;
    gap: 8px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }}
  .verdict {{
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .flags {{
    font-size: 0.7rem;
    color: #f97316;
    margin-bottom: 6px;
  }}

  /* メトリクスバー */
  .metrics {{ display: flex; flex-direction: column; gap: 4px; }}
  .metric-row {{
    display: grid;
    grid-template-columns: 44px 1fr 38px;
    align-items: center;
    gap: 6px;
  }}
  .metric-label {{ font-size: 0.65rem; color: #64748b; }}
  .bar-bg {{
    background: #0f172a;
    border-radius: 3px;
    height: 5px;
    overflow: hidden;
  }}
  .bar {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
  .metric-val {{ font-size: 0.65rem; color: #94a3b8; text-align: right; }}

  /* モーダル */
  .modal {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    z-index: 1000;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 12px;
  }}
  .modal.open {{ display: flex; }}
  .modal img {{
    max-width: 92vw;
    max-height: 85vh;
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  }}
  .modal-name {{
    color: #cbd5e1;
    font-size: 0.85rem;
  }}
  .modal-close {{
    position: absolute;
    top: 16px;
    right: 20px;
    background: none;
    border: none;
    color: #94a3b8;
    font-size: 2rem;
    cursor: pointer;
    line-height: 1;
  }}
  .modal-close:hover {{ color: #f1f5f9; }}

  .hidden {{ display: none !important; }}

  /* カウンター */
  .counter {{
    padding: 8px 24px 0;
    color: #64748b;
    font-size: 0.8rem;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>Aesthetic Shadowing Agent — <span>Stage 1 評価レポート</span></h1>
  <div class="stats">
    <div class="stat-box">
      <div class="num">{total}</div>
      <div class="lbl">総ファイル</div>
    </div>
    <div class="stat-box stat-tp">
      <div class="num">{tp}</div>
      <div class="lbl">TP 正却下</div>
    </div>
    <div class="stat-box stat-tn">
      <div class="num">{tn}</div>
      <div class="lbl">TN 正通過</div>
    </div>
    <div class="stat-box stat-fp">
      <div class="num">{fp}</div>
      <div class="lbl">FP 誤検出</div>
    </div>
    <div class="stat-box stat-fn">
      <div class="num">{fn}</div>
      <div class="lbl">FN 見逃し</div>
    </div>
    <div class="stat-box stat-f1">
      <div class="num">{precision:.0%}</div>
      <div class="lbl">Precision</div>
    </div>
    <div class="stat-box stat-f1">
      <div class="num">{recall:.0%}</div>
      <div class="lbl">Recall</div>
    </div>
    <div class="stat-box stat-f1">
      <div class="num">{f1:.0%}</div>
      <div class="lbl">F1 Score</div>
    </div>
  </div>
  <div class="thresholds">
    閾値:
    <span>ピンボケ &lt; {blur_th}</span>
    <span>白飛び &gt; {blown_th:.0%}</span>
    <span>黒潰れ &gt; {dark_th:.0%}</span>
  </div>
  <div class="filters">
    <button class="filter-btn active" onclick="setFilter('all', this)">
      全件 ({total})
    </button>
    <button class="filter-btn fp-btn" onclick="setFilter('fp', this)">
      誤検出 FP ({fp})
    </button>
    <button class="filter-btn fn-btn" onclick="setFilter('fn', this)">
      見逃し FN ({fn})
    </button>
    <button class="filter-btn tp-btn" onclick="setFilter('tp', this)">
      TP ({tp})
    </button>
    <button class="filter-btn" onclick="setFilter('tn', this)">
      TN ({tn})
    </button>
    <button class="filter-btn" onclick="setFilter('mismatch', this)">
      食い違い ({fp + fn})
    </button>
  </div>
</div>

<div class="counter" id="counter">表示: {total} 件</div>

<div class="grid" id="grid">
{cards_html}
</div>

<!-- モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <img id="modal-img" src="" alt="">
  <div class="modal-name" id="modal-name"></div>
</div>

<script>
let currentFilter = 'all';

function setFilter(type, btn) {{
  currentFilter = type;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  let visible = 0;
  document.querySelectorAll('.card').forEach(card => {{
    const result = card.dataset.result;
    let show = false;
    if (type === 'all') show = true;
    else if (type === 'mismatch') show = result === 'fp' || result === 'fn';
    else show = result === type;

    card.classList.toggle('hidden', !show);
    if (show) visible++;
  }});

  document.getElementById('counter').textContent = '表示: ' + visible + ' 件';
}}

function openModal(url, name) {{
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = name;
  document.getElementById('modal').classList.add('open');
}}

function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal') || e.target.classList.contains('modal-close')) {{
    document.getElementById('modal').classList.remove('open');
    document.getElementById('modal-img').src = '';
  }}
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeModal();
}});
</script>
</body>
</html>'''


# ---------- メイン ----------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage1 HTMLレポート生成',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir', help='S2 JPEGフォルダ')
    parser.add_argument('xmp_dir', help='Lightroom XMP書き出しフォルダ')
    parser.add_argument('stage1_csv', help='Stage1 CSVファイル')
    parser.add_argument(
        '--output', default='stage1_report.html',
        help='出力HTMLファイル名',
    )
    parser.add_argument('--blur-threshold', type=float, default=80.0)
    parser.add_argument('--blown-threshold', type=float, default=0.03)
    parser.add_argument('--dark-threshold', type=float, default=0.05)
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    xmp_dir = Path(args.xmp_dir)
    csv_path = Path(args.stage1_csv)

    for p, name in [(jpeg_dir, 'JPEGフォルダ'), (xmp_dir, 'XMPフォルダ'), (csv_path, 'CSV')]:
        if not p.exists():
            print(f'エラー: {name} が見つかりません: {p}')
            sys.exit(1)

    xmp_data = load_xmp_dir(xmp_dir)
    csv_data = load_stage1_csv(csv_path)
    common_stems = sorted(set(xmp_data.keys()) & set(csv_data.keys()))

    tp = fn = fp = tn = 0
    rows = []

    for stem in common_stems:
        xmp = xmp_data[stem]
        s1 = csv_data[stem]
        human_rej = xmp['human_rejected']
        stage1_rej = s1['stage1_rejected']

        if human_rej and stage1_rej:
            result = 'TP'
            tp += 1
        elif human_rej and not stage1_rej:
            result = 'FN'
            fn += 1
        elif not human_rej and stage1_rej:
            result = 'FP'
            fp += 1
        else:
            result = 'TN'
            tn += 1

        jpeg_path = find_jpeg(jpeg_dir, stem)
        jpeg_url = jpeg_path.as_uri() if jpeg_path else ''

        rows.append({
            'stem': stem,
            'result': result,
            'human_rejected': human_rej,
            'stage1_rejected': stage1_rej,
            'pick': xmp['pick'],
            'rating': xmp['rating'],
            'blur_score': s1['blur_score'],
            'blown_pct': s1['blown_pct'],
            'dark_pct': s1['dark_pct'],
            'flags': s1['flags'],
            'jpeg_url': jpeg_url,
        })

    # FP → FN → TP → TN の順に並べる（問題のあるものを先頭へ）
    ORDER = {'FP': 0, 'FN': 1, 'TP': 2, 'TN': 3}
    rows.sort(key=lambda r: (ORDER[r['result']], r['stem']))

    stats = {'total': len(rows), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn}
    thresholds = {
        'blur': args.blur_threshold,
        'blown': args.blown_threshold,
        'dark': args.dark_threshold,
    }

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    html_content = generate_html(rows, stats, thresholds)
    output_path.write_text(html_content, encoding='utf-8')

    print(f'レポート生成完了: {output_path}')
    print(f'ファイル数: {len(rows)} / TP={tp} TN={tn} FP={fp} FN={fn}')
    print(f'\nブラウザで開く:')
    print(f'  open "{output_path}"')


if __name__ == '__main__':
    main()
