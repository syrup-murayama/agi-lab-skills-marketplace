#!/usr/bin/env python3
"""
Stage 2 Step A: シーングルーピング

処理フロー:
  1. EXIF DateTimeOriginal で時刻順ソート
  2. 30秒ウィンドウで「シーン候補」を一次グループ化
  3. グループ内で pHash + L*a*b*ヒストグラム相関により
     「見た目が変わった」と判断したら分割
  4. 正面顔 / 横顔 / 上半身の Haar カスケードで人物数を推定
  5. 各カットに position（first / last / middle / solo）と
     ボーナス重みを付与
  6. CSV 出力

使い方:
  python group.py <jpeg_dir> [オプション]

例:
  python group.py /path/to/S2_JPEG/ --output stage2_groups.csv
"""

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image, ExifTags

# ---- 定数 ----
DEFAULT_TIME_GAP_SEC  = 30    # この秒数を超えたら別シーン候補
DEFAULT_PHASH_SPLIT   = 18    # pHash ハミング距離がこれ以上 → 分割
DEFAULT_HIST_SPLIT    = 0.40  # ヒストグラム相関がこれ未満（かつ pHash がグレー） → 分割補強
BONUS_FIRST           = 1.5   # シーン最初の1枚
BONUS_LAST            = 1.3   # シーン最後の1枚
BONUS_SOLO            = 1.5   # シーンが1枚だけ（単独ショット）
WEIGHT_MIDDLE         = 1.0   # 中間カット


# ---- データ構造 ----

@dataclass
class Shot:
    path: Path
    stem: str
    dt: datetime | None
    phash: imagehash.ImageHash
    hist: np.ndarray       # L*a*b* 3チャンネル 32-bin ヒストグラム結合
    person_count: int = 0
    group_id: int = -1
    position: str = 'middle'   # first / last / middle / solo
    bonus_weight: float = WEIGHT_MIDDLE


# ---- EXIF ----

def _exif_datetime(pil_img: Image.Image) -> datetime | None:
    try:
        exif_data = pil_img._getexif()
        if not exif_data:
            return None
        tag_map = {v: k for k, v in ExifTags.TAGS.items()}
        val = exif_data.get(tag_map.get('DateTimeOriginal'))
        if val:
            return datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
    except Exception:
        pass
    return None


# ---- 特徴量 ----

def _phash(pil_img: Image.Image) -> imagehash.ImageHash:
    return imagehash.phash(pil_img, hash_size=16)


def _lab_hist(bgr: np.ndarray) -> np.ndarray:
    """L*a*b* 色空間の各チャンネル 32-bin ヒストグラムを正規化して結合。"""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    parts = []
    for ch in range(3):
        h = cv2.calcHist([lab], [ch], None, [32], [0, 256])
        cv2.normalize(h, h)
        parts.append(h.flatten())
    return np.concatenate(parts)


def _hist_corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(cv2.compareHist(
        a.astype(np.float32).reshape(-1, 1),
        b.astype(np.float32).reshape(-1, 1),
        cv2.HISTCMP_CORREL,
    ))


# ---- 人物検出 ----

class PersonDetector:
    """
    正面顔 → 横顔の順に Haar カスケードで人物を検出し、
    0人 / 1人 / 複数人 の3分類で返す。

    上半身カスケードは複雑な背景で誤検知が多いため除外。
    顔が検出されない場合は「0人」とみなす（後段 Step B で補完予定）。
    """

    def __init__(self) -> None:
        base = cv2.data.haarcascades
        self._frontal = cv2.CascadeClassifier(base + 'haarcascade_frontalface_default.xml')
        self._profile = cv2.CascadeClassifier(base + 'haarcascade_profileface.xml')

    def count(self, bgr: np.ndarray) -> int:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        cv2.equalizeHist(gray, gray)

        frontal = self._frontal.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
        )
        n_frontal = len(frontal) if not isinstance(frontal, tuple) else 0
        if n_frontal > 0:
            return n_frontal

        # 横顔（正面で見つからない場合のみ）
        profile = self._profile.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
        )
        return len(profile) if not isinstance(profile, tuple) else 0


# ---- グルーピング ----

def _should_split(
    prev: Shot,
    curr: Shot,
    phash_thr: int,
    hist_thr: float,
) -> bool:
    dist = prev.phash - curr.phash
    corr = _hist_corr(prev.hist, curr.hist)
    # pHash が大きく変化 → 確実に別シーン
    if dist >= phash_thr:
        return True
    # pHash が中程度かつ色調も変化 → 別シーン
    if dist >= phash_thr * 0.6 and corr < hist_thr:
        return True
    return False


def assign_groups(
    shots: list[Shot],
    time_gap: int,
    phash_thr: int,
    hist_thr: float,
) -> None:
    """shots リストに group_id をインプレースで付与する。"""
    if not shots:
        return
    gid = 0
    shots[0].group_id = gid
    for i in range(1, len(shots)):
        prev, curr = shots[i - 1], shots[i]

        time_break = False
        if prev.dt and curr.dt:
            time_break = (curr.dt - prev.dt).total_seconds() > time_gap

        visual_break = _should_split(prev, curr, phash_thr, hist_thr)

        if time_break or visual_break:
            gid += 1
        curr.group_id = gid


def assign_positions(shots: list[Shot]) -> None:
    """group_id ごとに first/last/middle/solo とボーナスをインプレースで付与する。"""
    by_group: dict[int, list[Shot]] = {}
    for s in shots:
        by_group.setdefault(s.group_id, []).append(s)

    for members in by_group.values():
        if len(members) == 1:
            members[0].position = 'solo'
            members[0].bonus_weight = BONUS_SOLO
        else:
            members[0].position = 'first'
            members[0].bonus_weight = BONUS_FIRST
            members[-1].position = 'last'
            members[-1].bonus_weight = BONUS_LAST
            for m in members[1:-1]:
                m.position = 'middle'
                m.bonus_weight = WEIGHT_MIDDLE


# ---- 読み込み ----

def load_shots(
    jpeg_dir: Path,
    detector: PersonDetector,
    verbose: bool,
) -> list[Shot]:
    jpeg_files = sorted(
        list(jpeg_dir.glob('*.JPG')) + list(jpeg_dir.glob('*.jpg'))
    )
    if not jpeg_files:
        print(f'エラー: JPEGが見つかりません: {jpeg_dir}')
        sys.exit(1)

    shots = []
    t0 = time.time()

    for i, p in enumerate(jpeg_files):
        try:
            pil_img = Image.open(p)
            dt  = _exif_datetime(pil_img)
            ph  = _phash(pil_img)
        except Exception as e:
            print(f'  (PIL失敗) {p.name}: {e}')
            continue

        bgr = cv2.imread(str(p))
        if bgr is None:
            print(f'  (cv2読込失敗) {p.name}')
            continue

        hist = _lab_hist(bgr)
        n_persons = detector.count(bgr)

        shots.append(Shot(
            path=p, stem=p.stem, dt=dt, phash=ph,
            hist=hist, person_count=n_persons,
        ))

        if verbose and (i + 1) % 50 == 0:
            print(f'  {i + 1}/{len(jpeg_files)} ({time.time() - t0:.1f}s)')

    return shots


# ---- メイン ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage 2 Step A: シーングルーピング',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir')
    parser.add_argument('--output',      default='stage2_groups.csv')
    parser.add_argument('--time-gap',    type=int,   default=DEFAULT_TIME_GAP_SEC)
    parser.add_argument('--phash-split', type=int,   default=DEFAULT_PHASH_SPLIT)
    parser.add_argument('--hist-split',  type=float, default=DEFAULT_HIST_SPLIT)
    parser.add_argument('--verbose',     action='store_true')
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.exists():
        print(f'エラー: {jpeg_dir}')
        sys.exit(1)

    print('\n=== Aesthetic Shadowing Agent - Stage 2 Step A ===')
    print(f'対象: {jpeg_dir}')
    print(f'時刻ギャップ: {args.time_gap}s  '
          f'pHash閾値: {args.phash_split}  '
          f'ヒスト相関閾値: {args.hist_split}')
    print()

    detector = PersonDetector()

    t_start = time.time()
    shots = load_shots(jpeg_dir, detector, args.verbose)
    t_load = time.time() - t_start
    print(f'{len(shots)} ファイル読み込み完了 ({t_load:.1f}s / '
          f'{t_load / len(shots) * 100:.1f}s per 100枚)')

    # 時刻順ソート（EXIF なしは末尾）
    shots.sort(key=lambda s: (s.dt is None, s.dt or datetime.min, s.stem))

    assign_groups(shots, args.time_gap, args.phash_split, args.hist_split)
    assign_positions(shots)

    n_groups = max(s.group_id for s in shots) + 1
    t_total = time.time() - t_start

    # ---- サマリー ----
    print()
    print(f'グループ数: {n_groups}  /  ファイル数: {len(shots)}  /  総処理時間: {t_total:.1f}s')
    print()
    print(f'{"GID":>4}  {"枚":>3}  {"人":>3}  {"開始時刻":10}  {"最初":24}  {"最後":24}')
    print('─' * 78)

    sorted_shots = sorted(shots, key=lambda s: s.group_id)
    for gid, it in groupby(sorted_shots, key=lambda s: s.group_id):
        members = list(it)
        avg_persons = sum(m.person_count for m in members) / len(members)
        dt_str = members[0].dt.strftime('%H:%M:%S') if members[0].dt else '?'
        first  = members[0].stem
        last   = members[-1].stem if len(members) > 1 else '─'
        print(f'{gid:>4}  {len(members):>3}枚  {avg_persons:>3.1f}人  '
              f'{dt_str:10}  {first:24}  {last:24}')

    # ---- CSV ----
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = jpeg_dir.parent / output_path

    group_sizes = {s.group_id: 0 for s in shots}
    for s in shots:
        group_sizes[s.group_id] += 1

    fieldnames = [
        'file', 'datetime', 'group_id', 'group_size',
        'position', 'bonus_weight', 'person_count', 'phash',
    ]
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in sorted(shots, key=lambda s: (s.group_id, s.dt or datetime.min, s.stem)):
            writer.writerow({
                'file':         s.stem + '.JPG',
                'datetime':     s.dt.isoformat() if s.dt else '',
                'group_id':     s.group_id,
                'group_size':   group_sizes[s.group_id],
                'position':     s.position,
                'bonus_weight': s.bonus_weight,
                'person_count': s.person_count,
                'phash':        str(s.phash),
            })

    print()
    print(f'CSV: {output_path}')


if __name__ == '__main__':
    main()
