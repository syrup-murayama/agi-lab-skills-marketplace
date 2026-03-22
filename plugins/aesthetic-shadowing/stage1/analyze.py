#!/usr/bin/env python3
"""
Stage 1: 完全露出ミスフィルター
S2 JPEGを解析し、真っ白（完全白飛び）・真っ黒（完全黒潰れ）のカットだけを除外する。
フォーカス（ブレ・ピントずれ）の判定は Stage2 の technical_score に委ねる。

除外フラグが立ったカットに対応するCR3向けXMP Sidecarファイルを書き出す。
"""

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

# --- デフォルト閾値 ---
# "完全な撮影失敗カット"だけを絶対除外する設計。
# フォーカス評価はStage2のtechnical_score（sharpness_score）に委ねる。
#
# 白飛び: ピクセルの80%以上が輝度253以上 = ほぼ真っ白な失敗カット
# 黒潰れ: ピクセルの80%以上が輝度2以下  = ほぼ真っ黒な失敗カット
DEFAULT_BLOWN_THRESHOLD = 0.80   # 白飛び率がこれ超 = 完全白飛び
DEFAULT_DARK_THRESHOLD  = 0.80   # 黒潰れ率がこれ超 = 完全黒潰れ


def analyze_exposure(
    gray: np.ndarray,
    blown_threshold: float,
    dark_threshold: float,
) -> tuple[float, float, bool, bool]:
    """
    ヒストグラムで完全白飛び・完全黒潰れを評価する。
    """
    total = gray.size
    blown_ratio = float(np.sum(gray >= 253)) / total
    dark_ratio = float(np.sum(gray <= 2)) / total
    return blown_ratio, dark_ratio, blown_ratio > blown_threshold, dark_ratio > dark_threshold


def write_rejection_xmp(xmp_path: Path) -> None:
    """Lightroom互換のXMP Sidecarを書き出す（pick=-1 = 却下フラグ）。"""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Aesthetic Shadowing Agent">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about=""\n'
        '      xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        '      xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"\n'
        '      xmpDM:pick="-1"/>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    )
    xmp_path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage 1: 完全な露出ミスカット（真っ白・真っ黒）のみを除外',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir', help='S2 JPEGが入ったフォルダ')
    parser.add_argument('cr3_dir', help='CR3が入ったフォルダ（XMPをここに書き出す）')
    parser.add_argument(
        '--blown-threshold', type=float, default=DEFAULT_BLOWN_THRESHOLD,
        help='白飛びピクセル比率の閾値（0.80=80%%以上が白飛びで除外）',
    )
    parser.add_argument(
        '--dark-threshold', type=float, default=DEFAULT_DARK_THRESHOLD,
        help='黒潰れピクセル比率の閾値（0.80=80%%以上が黒潰れで除外）',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='XMPを書き出さず結果だけ表示する',
    )
    parser.add_argument(
        '--demo', action='store_true',
        help='デモモード: 実際の画像解析をスキップしサンプルデータを生成する',
    )
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    cr3_dir = Path(args.cr3_dir)

    if not jpeg_dir.exists():
        print(f'エラー: JPEGフォルダが見つかりません: {jpeg_dir}')
        sys.exit(1)
    if not cr3_dir.exists():
        print(f'エラー: CR3フォルダが見つかりません: {cr3_dir}')
        sys.exit(1)

    jpeg_files = sorted(
        list(jpeg_dir.glob('*.JPG')) + list(jpeg_dir.glob('*.jpg'))
    )
    if not jpeg_files:
        print(f'エラー: JPEGファイルが見つかりません: {jpeg_dir}')
        sys.exit(1)

    print(f'\n=== Aesthetic Shadowing Agent - Stage 1 ===')
    print(f'対象: {len(jpeg_files)} ファイル')
    print(f'閾値: 白飛び={args.blown_threshold:.0%} / 黒潰れ={args.dark_threshold:.0%}')
    print(f'（フォーカス評価はStage2のtechnical_scoreに委ねる設計）')
    if args.dry_run:
        print('（ドライランモード: XMPは書き出しません）')
    if args.demo:
        print('（デモモード: 実際の画像解析をスキップします）')
    print()

    header = f'{"ファイル名":<28} {"白飛び%":>7} {"黒潰れ%":>7}  判定'
    print(header)
    print('-' * 55)

    results = []
    rejected_count = 0

    if args.demo:
        import hashlib, time as _time
        for jpeg_path in jpeg_files:
            h = int(hashlib.md5(jpeg_path.name.encode()).hexdigest()[:8], 16)
            blown = (h % 80) / 1000          # 0.0〜8.0%
            dark  = ((h >> 8) % 60) / 1000   # 0.0〜6.0%
            print(f'{jpeg_path.name:<28} {blown:>6.1%} {dark:>6.1%}  OK')
            results.append({
                'file': jpeg_path.name,
                'blown_pct': round(blown * 100, 2),
                'dark_pct':  round(dark  * 100, 2),
                'flags':     'OK',
                'rejected':  False,
            })
            _time.sleep(0.005)
    else:
        for jpeg_path in jpeg_files:
            img = cv2.imread(str(jpeg_path))
            if img is None:
                print(f'  (読込失敗) {jpeg_path.name}')
                continue

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            blown_ratio, dark_ratio, is_blown, is_dark = analyze_exposure(
                gray, args.blown_threshold, args.dark_threshold
            )

            flags = []
            if is_blown:
                flags.append('白飛び')
            if is_dark:
                flags.append('黒潰れ')

            is_rejected = bool(flags)
            verdict = '  NG: ' + ' / '.join(flags) if is_rejected else '  OK'

            print(f'{jpeg_path.name:<28} {blown_ratio:>6.1%} {dark_ratio:>6.1%}  {verdict}')

            if is_rejected:
                rejected_count += 1
                if not args.dry_run:
                    xmp_path = cr3_dir / (jpeg_path.stem + '.xmp')
                    write_rejection_xmp(xmp_path)

            results.append({
                'file': jpeg_path.name,
                'blown_pct': round(blown_ratio * 100, 2),
                'dark_pct': round(dark_ratio * 100, 2),
                'flags': ' / '.join(flags) if flags else 'OK',
                'rejected': is_rejected,
            })

    print('-' * 55)
    total = len(results)
    print(f'\n結果: {total} ファイル中 {rejected_count} 件を除外 ({rejected_count / total:.1%})')

    # CSV出力
    csv_path = cr3_dir / 'stage1_results.csv'
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    if not args.dry_run:
        print(f'XMP Sidecar: {cr3_dir} に書き出しました')
    print(f'詳細CSV: {csv_path}')


if __name__ == '__main__':
    main()
