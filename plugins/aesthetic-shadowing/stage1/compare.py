#!/usr/bin/env python3
"""
Stage 1 評価比較ツール

Lightroomで書き出したXMP Sidecar（手動評価）と
Stage1 CSVの自動判定を照合し、精度を計測する。

使い方:
  python compare.py <xmp_dir> <stage1_csv>

例:
  python compare.py \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/xmp_output/  \
    /Users/daisuke/Pictures/ASA-test-data/v1.0.1/xmp_output/stage1_results.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path


def parse_xmp_verdict(xmp_path: Path) -> dict:
    """XMPファイルから手動評価（pick / rating）を抽出する。"""
    text = xmp_path.read_text(encoding='utf-8')

    pick_match = re.search(r'xmpDM:pick="(-?\d+)"', text)
    rating_match = re.search(r'xmp:Rating="(-?\d+)"', text)

    pick = int(pick_match.group(1)) if pick_match else 0
    rating = int(rating_match.group(1)) if rating_match else 0

    # pick=-1 = Lightroom「却下」フラグ = Stage1が捕まえるべき対象
    human_rejected = pick == -1

    return {
        'stem': xmp_path.stem,
        'pick': pick,
        'rating': rating,
        'human_rejected': human_rejected,
    }


def load_xmp_dir(xmp_dir: Path) -> dict[str, dict]:
    """XMPディレクトリ全体を読み込み、stem → verdict の辞書を返す。"""
    results = {}
    for xmp_path in sorted(xmp_dir.glob('*.xmp')):
        verdict = parse_xmp_verdict(xmp_path)
        results[verdict['stem']] = verdict
    return results


def load_stage1_csv(csv_path: Path) -> dict[str, dict]:
    """Stage1 CSVを読み込み、stem → row の辞書を返す。"""
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage1自動判定 vs Lightroom手動評価の比較',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('xmp_dir', help='LightroomのXMP書き出しフォルダ')
    parser.add_argument('stage1_csv', help='Stage1が出力したCSVファイル')
    parser.add_argument(
        '--show-all', action='store_true',
        help='全ファイルを表示（デフォルトは食い違いのみ）',
    )
    args = parser.parse_args()

    xmp_dir = Path(args.xmp_dir)
    csv_path = Path(args.stage1_csv)

    if not xmp_dir.exists():
        print(f'エラー: XMPフォルダが見つかりません: {xmp_dir}')
        sys.exit(1)
    if not csv_path.exists():
        print(f'エラー: CSVが見つかりません: {csv_path}')
        sys.exit(1)

    xmp_data = load_xmp_dir(xmp_dir)
    csv_data = load_stage1_csv(csv_path)

    # 共通ファイルのみ照合
    common_stems = sorted(set(xmp_data.keys()) & set(csv_data.keys()))
    only_in_xmp = set(xmp_data.keys()) - set(csv_data.keys())
    only_in_csv = set(csv_data.keys()) - set(xmp_data.keys())

    if only_in_xmp:
        print(f'注意: XMPのみ存在（CSV未処理）: {len(only_in_xmp)} ファイル')
    if only_in_csv:
        print(f'注意: CSVのみ存在（XMP未書き出し）: {len(only_in_csv)} ファイル')

    # 混同行列
    tp = fn = fp = tn = 0
    rows = []

    for stem in common_stems:
        xmp = xmp_data[stem]
        csv_row = csv_data[stem]
        human_rej = xmp['human_rejected']
        stage1_rej = csv_row['stage1_rejected']

        if human_rej and stage1_rej:
            result = 'TP'
            tp += 1
        elif human_rej and not stage1_rej:
            result = 'FN'  # 見逃し（人間は却下、Stage1はOK）
            fn += 1
        elif not human_rej and stage1_rej:
            result = 'FP'  # 誤検出（人間はOK、Stage1は却下）
            fp += 1
        else:
            result = 'TN'
            tn += 1

        rows.append({
            'stem': stem,
            'result': result,
            'human_rej': human_rej,
            'stage1_rej': stage1_rej,
            'pick': xmp['pick'],
            'rating': xmp['rating'],
            'blur_score': csv_row['blur_score'],
            'blown_pct': csv_row['blown_pct'],
            'dark_pct': csv_row['dark_pct'],
            'flags': csv_row['flags'],
        })

    # --- 出力 ---
    print(f'\n=== Stage1 精度レポート ===')
    print(f'照合ファイル数: {len(common_stems)}')
    print()

    # 混同行列
    total = tp + fn + fp + tn
    print('【混同行列】')
    print(f'               Stage1:却下  Stage1:OK')
    print(f'  人間:却下      TP={tp:>4}     FN={fn:>4}  （見逃し）')
    print(f'  人間:OK        FP={fp:>4}     TN={tn:>4}  （誤検出）')
    print()

    precision = tp / (tp + fp) if (tp + fp) > 0 else float('nan')
    recall    = tp / (tp + fn) if (tp + fn) > 0 else float('nan')
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else float('nan')
    accuracy  = (tp + tn) / total if total > 0 else float('nan')

    print('【スコア】')
    print(f'  Precision（誤検出の少なさ）: {precision:.1%}')
    print(f'  Recall   （見逃しの少なさ）: {recall:.1%}')
    print(f'  F1 Score                   : {f1:.1%}')
    print(f'  Accuracy                   : {accuracy:.1%}')
    print()

    # 食い違いリスト
    discrepancies = [r for r in rows if r['result'] in ('FP', 'FN')]
    if discrepancies:
        print('【食い違い一覧】')
        header = f'{"ファイル":28} {"判定":4}  {"ピンボケ":>8} {"白飛び%":>7} {"黒潰れ%":>7}  {"Stage1フラグ"}'
        print(header)
        print('-' * 80)
        for r in discrepancies:
            print(
                f'{r["stem"]:<28} {r["result"]:4}  '
                f'{r["blur_score"]:>8.1f} {r["blown_pct"]:>6.1f}% {r["dark_pct"]:>6.1f}%  '
                f'{r["flags"]}'
            )
        print()

    # 全ファイル表示
    if args.show_all:
        print('【全ファイル一覧】')
        header = f'{"ファイル":28} {"判定":4}  {"人間":5} {"S1":5}  {"ピンボケ":>8} {"白飛び%":>7} {"黒潰れ%":>7}  {"フラグ"}'
        print(header)
        print('-' * 90)
        for r in rows:
            h = 'reject' if r['human_rej'] else 'OK    '
            s = 'reject' if r['stage1_rej'] else 'OK    '
            print(
                f'{r["stem"]:<28} {r["result"]:4}  {h} {s}  '
                f'{r["blur_score"]:>8.1f} {r["blown_pct"]:>6.1f}% {r["dark_pct"]:>6.1f}%  '
                f'{r["flags"]}'
            )
        print()

    # 閾値調整ヒント（FP / FN のスコアを表示）
    fps = [r for r in rows if r['result'] == 'FP']
    fns = [r for r in rows if r['result'] == 'FN']

    if fps:
        print('【誤検出(FP)のスコア — 閾値を緩めると解消できる可能性】')
        for r in fps:
            print(f'  {r["stem"]:28}  ピンボケ={r["blur_score"]:.1f}  白飛び={r["blown_pct"]:.1f}%  黒潰れ={r["dark_pct"]:.1f}%  flags={r["flags"]}')
        print()

    if fns:
        print('【見逃し(FN)のスコア — 閾値を厳しくすると解消できる可能性】')
        for r in fns:
            print(f'  {r["stem"]:28}  ピンボケ={r["blur_score"]:.1f}  白飛び={r["blown_pct"]:.1f}%  黒潰れ={r["dark_pct"]:.1f}%  flags={r["flags"]}')
        print()

    if not discrepancies:
        print('食い違いなし。Stage1の判定は手動評価と完全一致しています。')


if __name__ == '__main__':
    main()
