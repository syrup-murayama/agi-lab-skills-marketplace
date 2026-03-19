#!/usr/bin/env python3
"""
Stage 1: Technical quality filter
S2 JPEGを解析し、ピンボケ・白飛び・黒潰れのカットを検出する。
除外フラグが立ったカットに対応するCR3向けXMP Sidecarファイルを書き出す。
"""

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

# --- デフォルト閾値（テスト後に調整する） ---
DEFAULT_BLUR_THRESHOLD = 80.0    # ラプラシアン分散がこれ未満 = ピンボケ
DEFAULT_BLOWN_THRESHOLD = 0.03   # 輝度最大付近のピクセルが3%超 = 白飛び
DEFAULT_DARK_THRESHOLD = 0.05    # 輝度最小付近のピクセルが5%超 = 黒潰れ


def analyze_blur(gray: np.ndarray, threshold: float) -> tuple[float, bool]:
    """
    画像中央60%領域のラプラシアン分散でフォーカスを評価する。
    値が低いほどぼやけている。
    """
    h, w = gray.shape
    margin_y = int(h * 0.2)
    margin_x = int(w * 0.2)
    subject_region = gray[margin_y:h - margin_y, margin_x:w - margin_x]
    score = float(cv2.Laplacian(subject_region, cv2.CV_64F).var())
    return score, score < threshold


def analyze_exposure(
    gray: np.ndarray,
    blown_threshold: float,
    dark_threshold: float,
) -> tuple[float, float, bool, bool]:
    """
    ヒストグラムで白飛び・黒潰れを評価する。
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
        description='Stage 1: S2 JPEGの技術品質フィルター',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir', help='S2 JPEGが入ったフォルダ')
    parser.add_argument('cr3_dir', help='CR3が入ったフォルダ（XMPをここに書き出す）')
    parser.add_argument(
        '--blur-threshold', type=float, default=DEFAULT_BLUR_THRESHOLD,
        help='ラプラシアン分散の閾値（低いほど厳しい）',
    )
    parser.add_argument(
        '--blown-threshold', type=float, default=DEFAULT_BLOWN_THRESHOLD,
        help='白飛びピクセル比率の閾値',
    )
    parser.add_argument(
        '--dark-threshold', type=float, default=DEFAULT_DARK_THRESHOLD,
        help='黒潰れピクセル比率の閾値',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='XMPを書き出さず結果だけ表示する',
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
    print(f'閾値: ピンボケ={args.blur_threshold} / 白飛び={args.blown_threshold:.0%} / 黒潰れ={args.dark_threshold:.0%}')
    if args.dry_run:
        print('（ドライランモード: XMPは書き出しません）')
    print()

    header = f'{"ファイル名":<28} {"ピンボケ":>8} {"白飛び%":>7} {"黒潰れ%":>7}  判定'
    print(header)
    print('-' * 65)

    results = []
    rejected_count = 0

    for jpeg_path in jpeg_files:
        img = cv2.imread(str(jpeg_path))
        if img is None:
            print(f'  (読込失敗) {jpeg_path.name}')
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        blur_score, is_blurry = analyze_blur(gray, args.blur_threshold)
        blown_ratio, dark_ratio, is_blown, is_dark = analyze_exposure(
            gray, args.blown_threshold, args.dark_threshold
        )

        flags = []
        if is_blurry:
            flags.append('ピンボケ')
        if is_blown:
            flags.append('白飛び')
        if is_dark:
            flags.append('黒潰れ')

        is_rejected = bool(flags)
        verdict = '  NG: ' + ' / '.join(flags) if is_rejected else '  OK'

        print(f'{jpeg_path.name:<28} {blur_score:>8.1f} {blown_ratio:>6.1%} {dark_ratio:>6.1%}  {verdict}')

        if is_rejected:
            rejected_count += 1
            if not args.dry_run:
                xmp_path = cr3_dir / (jpeg_path.stem + '.xmp')
                write_rejection_xmp(xmp_path)

        results.append({
            'file': jpeg_path.name,
            'blur_score': round(blur_score, 2),
            'blown_pct': round(blown_ratio * 100, 2),
            'dark_pct': round(dark_ratio * 100, 2),
            'flags': ' / '.join(flags) if flags else 'OK',
            'rejected': is_rejected,
        })

    print('-' * 65)
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
