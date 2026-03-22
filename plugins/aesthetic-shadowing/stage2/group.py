#!/usr/bin/env python3
"""
Stage 2 Step A: シーングルーピング + 技術スコアリング

処理フロー:
  1. EXIF DateTimeOriginal で時刻順ソート
  2. 30秒ウィンドウで「シーン候補」を一次グループ化
  3. グループ内で pHash + L*a*b*ヒストグラム相関により
     「見た目が変わった」と判断したら分割
  4. MediaPipe FaceLandmarker で人物数 + 瞳開き度 (EAR) を推定
  5. 各カットに position（first / last / middle / solo）を付与
     （--enable-position-bonus で旧来のボーナス重みを有効化可能）
  6. 技術スコアを算出（sharpness / exposure / technical）
  7. CSV 出力

使い方:
  python group.py <jpeg_dir> [オプション]

例:
  python group.py /path/to/S2_JPEG/ --output stage2_groups.csv
"""

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image, ExifTags

# ---- 定数 ----
DEFAULT_TIME_GAP_SEC   = 30    # この秒数を超えたら別シーン候補
DEFAULT_PHASH_SPLIT    = 18    # pHash ハミング距離がこれ以上 → 分割
DEFAULT_HIST_SPLIT     = 0.40  # ヒストグラム相関がこれ未満（かつ pHash がグレー） → 分割補強
DEFAULT_MIN_VISUAL_GAP = 5     # この秒数未満の連続ショットは pHash 分割しない
DEFAULT_SOLO_MERGE_GAP = 10    # 隣接 SOLO グループをこの秒数以内でマージ

# 旧来のポジションボーナス（--enable-position-bonus で有効）
BONUS_FIRST    = 1.5
BONUS_LAST     = 1.3
BONUS_SOLO     = 1.5
WEIGHT_MIDDLE  = 1.0

# 技術スコアの重み（eye_score なしの場合は sharpness+exposure で正規化）
WEIGHT_SHARPNESS = 0.55
WEIGHT_EXPOSURE  = 0.35
WEIGHT_EYE       = 0.10  # 瞳ボーナス（eye_score=None の場合は残り重みで正規化）

# リサイズ基準（速度優先）
ANALYSIS_SHORT_SIDE = 512


# ---- データ構造 ----

@dataclass
class Shot:
    path: Path
    stem: str
    dt: datetime | None
    phash: imagehash.ImageHash
    hist: np.ndarray           # L*a*b* 3チャンネル 32-bin ヒストグラム結合
    person_count: int = 0
    eye_score: float | None = None
    group_id: int = -1
    position: str = 'middle'   # first / last / middle / solo
    bonus_weight: float = 1.0
    sharpness_raw: float = 0.0  # ラプラシアン分散（グループ内正規化前）
    sharpness_score: float = 0.0  # グループ内相対スコア 0.0〜1.0
    exposure_score: float = 0.0   # 露出スコア 0.0〜1.0
    technical_score: float = 0.0  # 総合技術スコア 0.0〜1.0
    camera_rating: int = 0        # 0=未設定、1〜5=カメラ内XMPレーティング
    near_rated: bool = False       # 同グループの前後カットにcamera_rating>0があるか


# ---- カメラ内XMPレーティング ----

def read_camera_rating(jpeg_path: Path) -> int:
    """JPEGの先頭64KBからXMP埋め込みの <xmp:Rating>N</xmp:Rating> を読み取る。見つからなければ0を返す。"""
    try:
        with open(jpeg_path, 'rb') as f:
            chunk = f.read(65536)
        text = chunk.decode('utf-8', errors='ignore')
        m = re.search(r'<xmp:Rating>(\d+)</xmp:Rating>', text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


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


def _resize_for_analysis(bgr: np.ndarray) -> np.ndarray:
    """短辺が ANALYSIS_SHORT_SIDE px になるようリサイズ（既に小さければそのまま）。"""
    h, w = bgr.shape[:2]
    short = min(h, w)
    if short <= ANALYSIS_SHORT_SIDE:
        return bgr
    scale = ANALYSIS_SHORT_SIDE / short
    return cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _sharpness_raw(bgr: np.ndarray) -> float:
    """ラプラシアン分散でシャープネスを計算。短辺512pxにリサイズして速度優先。"""
    small = _resize_for_analysis(bgr)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _sharpness_subject(bgr: np.ndarray, face_rects: list[tuple[int,int,int,int]]) -> float:
    """
    被写体（顔ROI）優先のシャープネスを返す。

    - 顔ROIなし: 全体スコアをそのまま返す
    - 顔ROIあり:
        * 顔ROIシャープネス × 0.9 + 全体 × 0.1
        * 背景が被写体の2倍以上鮮明 → フォーカスが背景に移っている可能性あり
          → 顔スコアをさらに 0.6 倍にペナルティ（背景ボケとしてみなす）
    """
    whole = _sharpness_raw(bgr)
    if not face_rects:
        return whole

    gray_full = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    roi_scores = []
    for (x1, y1, x2, y2) in face_rects:
        roi = gray_full[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        if min(roi.shape[:2]) < 32:
            roi = cv2.resize(roi, (64, 64))
        roi_scores.append(float(cv2.Laplacian(roi, cv2.CV_64F).var()))

    if not roi_scores:
        return whole

    subject_sharp = sum(roi_scores) / len(roi_scores)

    # 背景が被写体より2倍以上鮮明 → フォーカスが背景に移っている
    # 例: 竹の壁・草木など高コントラスト背景で被写体がボケているケース
    focus_penalty = 1.0
    if subject_sharp > 0 and whole / subject_sharp > 2.0:
        focus_penalty = 0.6

    return subject_sharp * 0.9 * focus_penalty + whole * 0.1


def _exposure_score(bgr: np.ndarray) -> float:
    """
    白飛び・黒潰れ率からスコアを計算。
    - 白飛び（輝度255）が全ピクセルの5%超 → ペナルティ
    - 黒潰れ（輝度0）が全ピクセルの5%超 → ペナルティ
    各超過1%あたり0.2のペナルティ（5%超過でスコア0）
    """
    small = _resize_for_analysis(bgr)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    total = gray.size
    overexp = float(np.sum(gray == 255)) / total
    underexp = float(np.sum(gray == 0)) / total
    score = 1.0
    if overexp > 0.05:
        score -= min(1.0, (overexp - 0.05) * 20)
    if underexp > 0.05:
        score -= min(1.0, (underexp - 0.05) * 20)
    return max(0.0, score)


# ---- 人物検出 ----

# FaceLandmarker 用目ランドマーク (478点モデル)
_LEFT_EYE_IDX  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE_IDX = [33,  160, 158, 133, 153, 144]
_EAR_OPEN_THRESHOLD = 0.18   # EAR がこの値以上なら「目が開いている」

# モデルファイルパス（同ディレクトリに配置）
_STAGE2_DIR = Path(__file__).parent
_LANDMARKER_MODEL = _STAGE2_DIR / 'face_landmarker.task'
_DETECTOR_MODEL   = _STAGE2_DIR / 'blaze_face.tflite'


def _ear(landmarks, indices: list[int]) -> float:
    """Eye Aspect Ratio (EAR) を計算する。"""
    pts = [(landmarks[i].x, landmarks[i].y) for i in indices]

    def d(a: tuple, b: tuple) -> float:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    vert = (d(pts[1], pts[5]) + d(pts[2], pts[4])) / 2.0
    horiz = d(pts[0], pts[3])
    return vert / horiz if horiz > 1e-6 else 0.0


def _eye_sep_score(detection, img_w: int) -> float:
    """
    BlazeFace keypoints から目の間隔比で顔向きを推定するフォールバック。
    eye_sep / face_width が大きいほど正面顔 → 高スコア。
    keypoints: [right_eye, left_eye, nose, mouth, right_ear, left_ear]  (正規化座標)
    """
    kpts = detection.keypoints
    if len(kpts) < 2:
        return 0.0
    re = kpts[0]  # right eye (x, y 正規化)
    le = kpts[1]  # left eye
    sep = ((le.x - re.x) ** 2 + (le.y - re.y) ** 2) ** 0.5
    bb = detection.bounding_box
    face_w_norm = bb.width / img_w if img_w > 0 else 1.0
    ratio = sep / face_w_norm if face_w_norm > 0 else 0.0
    if ratio >= 0.35:
        return 1.0    # 両目がはっきり離れている → 正面
    elif ratio >= 0.18:
        return 0.5    # 斜め顔
    else:
        return 0.2    # 横顔 / 俯き


class PersonDetector:
    """
    2段階 MediaPipe 検出:
      Stage1: BlazeFace (FaceDetector) で顔BB + eye keypoints を取得
      Stage2: 顔BB切り出し → FaceLandmarker で EAR 計算（失敗時は keypoint比率でフォールバック）

    person_count: 検出された顔の数
    eye_score:    顔なし=None
                  両目開き(EAR/正面)=1.0, 片目/斜め=0.5, 俯き/横顔=0.2, 閉眼=0.0
    """

    def __init__(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision as mp_vision

        for path, label in [(_DETECTOR_MODEL, 'BlazeFace'), (_LANDMARKER_MODEL, 'FaceLandmarker')]:
            if not path.exists():
                raise FileNotFoundError(
                    f'{label} モデルが見つかりません: {path}\n'
                    'setup.sh を実行してモデルをダウンロードしてください。'
                )

        det_opts = mp_vision.FaceDetectorOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(_DETECTOR_MODEL)),
            min_detection_confidence=0.2,
        )
        self._detector = mp_vision.FaceDetector.create_from_options(det_opts)

        lm_opts = mp_vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(_LANDMARKER_MODEL)),
            num_faces=1,
            min_face_detection_confidence=0.1,
            min_face_presence_confidence=0.1,
            min_tracking_confidence=0.1,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(lm_opts)
        self._mp = mp

    def detect(self, bgr: np.ndarray) -> tuple[int, float | None, list[tuple[int,int,int,int]]]:
        """
        顔数・eye_score・顔BB一覧を返す。
        face_rects: [(x1, y1, x2, y2), ...] ピクセル座標
        """
        mp = self._mp
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        det_result = self._detector.detect(mp_img)
        n_faces = len(det_result.detections)
        if n_faces == 0:
            return 0, None, []

        scores = []
        face_rects = []
        for det in det_result.detections:
            bb = det.bounding_box
            # 顔BBをパディングなしで記録（シャープネス計算用）
            fx1 = max(0, bb.origin_x)
            fy1 = max(0, bb.origin_y)
            fx2 = min(w, bb.origin_x + bb.width)
            fy2 = min(h, bb.origin_y + bb.height)
            face_rects.append((fx1, fy1, fx2, fy2))

            # FaceLandmarker 用にパディングありでクロップ
            pad = int(max(bb.width, bb.height) * 0.4)
            x1 = max(0, bb.origin_x - pad)
            y1 = max(0, bb.origin_y - pad)
            x2 = min(w, bb.origin_x + bb.width + pad)
            y2 = min(h, bb.origin_y + bb.height + pad)
            crop = np.ascontiguousarray(rgb[y1:y2, x1:x2])
            mp_crop = mp.Image(image_format=mp.ImageFormat.SRGB, data=crop)
            lm_result = self._landmarker.detect(mp_crop)

            if lm_result.face_landmarks:
                lms = lm_result.face_landmarks[0]
                l_ear = _ear(lms, _LEFT_EYE_IDX)
                r_ear = _ear(lms, _RIGHT_EYE_IDX)
                l_open = l_ear >= _EAR_OPEN_THRESHOLD
                r_open = r_ear >= _EAR_OPEN_THRESHOLD
                if l_open and r_open:
                    scores.append(1.0)
                elif l_open or r_open:
                    scores.append(0.5)
                else:
                    scores.append(0.0)
            else:
                scores.append(_eye_sep_score(det, w))

        return n_faces, sum(scores) / len(scores), face_rects

    def count_and_eye_score(self, bgr: np.ndarray) -> tuple[int, float | None]:
        """後方互換用ラッパー。"""
        n, eye, _ = self.detect(bgr)
        return n, eye


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
    min_visual_gap: float = DEFAULT_MIN_VISUAL_GAP,
) -> None:
    """shots リストに group_id をインプレースで付与する。"""
    if not shots:
        return
    gid = 0
    shots[0].group_id = gid
    for i in range(1, len(shots)):
        prev, curr = shots[i - 1], shots[i]

        elapsed = None
        time_break = False
        if prev.dt and curr.dt:
            elapsed = (curr.dt - prev.dt).total_seconds()
            time_break = elapsed > time_gap

        # 近接ショット（min_visual_gap 秒未満）は視覚変化で分割しない
        # （同じ被写体を連続撮影している可能性が高い）
        visual_break = False
        if elapsed is None or elapsed >= min_visual_gap:
            visual_break = _should_split(prev, curr, phash_thr, hist_thr)

        if time_break or visual_break:
            gid += 1
        curr.group_id = gid


def merge_solo_groups(shots: list[Shot], max_gap_sec: float) -> None:
    """隣接するSOLOグループを時刻差でマージする後処理。

    group_id を再採番し、assign_positions を呼び直す前提で使う。
    """
    if max_gap_sec <= 0:
        return

    by_group: dict[int, list[Shot]] = {}
    for s in shots:
        by_group.setdefault(s.group_id, []).append(s)

    # 時刻順にグループIDを並べる
    group_ids = sorted(
        by_group.keys(),
        key=lambda g: (by_group[g][0].dt is None, by_group[g][0].dt or datetime.min),
    )

    # 古い gid → 新しい gid のマッピングを構築
    remap: dict[int, int] = {}
    new_gid = 0
    i = 0
    while i < len(group_ids):
        gid = group_ids[i]
        remap[gid] = new_gid
        merged = list(by_group[gid])  # このグループのショット群

        # 次の SOLO グループとマージし続ける
        while len(merged) == 1 and i + 1 < len(group_ids):
            next_gid = group_ids[i + 1]
            next_members = by_group[next_gid]
            # どちらも dt がある場合のみ時刻差を判定
            if merged[-1].dt and next_members[0].dt:
                gap = (next_members[0].dt - merged[-1].dt).total_seconds()
                if gap <= max_gap_sec:
                    remap[next_gid] = new_gid
                    merged.extend(next_members)
                    i += 1
                    continue
            break

        new_gid += 1
        i += 1

    for s in shots:
        s.group_id = remap[s.group_id]


def assign_positions(shots: list[Shot], enable_bonus: bool = False) -> None:
    """group_id ごとに first/last/middle/solo をインプレースで付与する。

    enable_bonus=True のとき旧来のボーナス重みを適用。
    デフォルトは全カット bonus_weight=1.0（フラット）。
    """
    by_group: dict[int, list[Shot]] = {}
    for s in shots:
        by_group.setdefault(s.group_id, []).append(s)

    for members in by_group.values():
        if len(members) == 1:
            members[0].position = 'solo'
            members[0].bonus_weight = BONUS_SOLO if enable_bonus else 1.0
        else:
            members[0].position = 'first'
            members[0].bonus_weight = BONUS_FIRST if enable_bonus else 1.0
            members[-1].position = 'last'
            members[-1].bonus_weight = BONUS_LAST if enable_bonus else 1.0
            for m in members[1:-1]:
                m.position = 'middle'
                m.bonus_weight = WEIGHT_MIDDLE


def compute_technical_scores(shots: list[Shot]) -> None:
    """グループ内でシャープネスを正規化し、technical_score をインプレースで計算する。"""
    by_group: dict[int, list[Shot]] = {}
    for s in shots:
        by_group.setdefault(s.group_id, []).append(s)

    for members in by_group.values():
        max_sharp = max(m.sharpness_raw for m in members)
        for m in members:
            m.sharpness_score = m.sharpness_raw / max_sharp if max_sharp > 0 else 0.0
            if m.eye_score is not None:
                # 瞳スコアあり: 3因子で計算
                m.technical_score = (
                    m.sharpness_score * WEIGHT_SHARPNESS
                    + m.exposure_score * WEIGHT_EXPOSURE
                    + m.eye_score      * WEIGHT_EYE
                )
            else:
                # 人物なし / 検出失敗: sharpness+exposure のみ（重みを正規化）
                w_total = WEIGHT_SHARPNESS + WEIGHT_EXPOSURE
                m.technical_score = (
                    m.sharpness_score * WEIGHT_SHARPNESS / w_total
                    + m.exposure_score * WEIGHT_EXPOSURE  / w_total
                )


def assign_near_rated(shots: list[Shot]) -> None:
    """グループ内でcamera_rating>0のショットの前後1枚にnear_rated=Trueを設定する。"""
    by_group: dict[int, list[Shot]] = {}
    for s in shots:
        by_group.setdefault(s.group_id, []).append(s)

    for members in by_group.values():
        for i, m in enumerate(members):
            if m.camera_rating > 0:
                if i > 0:
                    members[i - 1].near_rated = True
                if i < len(members) - 1:
                    members[i + 1].near_rated = True


# ---- 読み込み ----

def load_rejected_stems(xmp_dir: Path) -> set[str]:
    """XMPファイルからpick=-1またはRating=-1のファイル名（stem）を返す。"""
    rejected = set()
    for xmp_path in xmp_dir.glob('*.xmp'):
        content = xmp_path.read_text(encoding='utf-8', errors='ignore')
        if 'xmpDM:pick="-1"' in content or 'xmp:Rating="-1"' in content:
            rejected.add(xmp_path.stem)
    return rejected


def load_shots(
    jpeg_dir: Path,
    detector: PersonDetector,
    verbose: bool,
    rejected_stems: set[str] | None = None,
    demo: bool = False,
) -> list[Shot]:
    import hashlib
    jpeg_files = sorted(
        list(jpeg_dir.glob('*.JPG')) + list(jpeg_dir.glob('*.jpg'))
    )
    if not jpeg_files:
        print(f'エラー: JPEGが見つかりません: {jpeg_dir}')
        sys.exit(1)

    if rejected_stems is None:
        rejected_stems = set()

    shots = []
    n_skipped = 0
    t0 = time.time()

    for i, p in enumerate(jpeg_files):
        if p.stem in rejected_stems:
            n_skipped += 1
            continue
        try:
            pil_img = Image.open(p)
            dt = _exif_datetime(pil_img)
        except Exception as e:
            print(f'  (PIL失敗) {p.name}: {e}')
            continue

        if demo:
            # 重い処理（pHash/MediaPipe/OpenCV）をスキップしてサンプル値を生成
            h = int(hashlib.md5(p.name.encode()).hexdigest()[:8], 16)
            ph         = imagehash.hex_to_hash('0' * 16)   # 全ショット同一 → 時刻のみでグループ化
            hist       = np.zeros(48, dtype=np.float32)
            n_persons  = 1 if (h % 3) > 0 else 0
            eye_score  = round(0.5 + (h % 50) / 100, 2) if n_persons > 0 else None
            sharp      = round(0.55 + (h % 40) / 100, 2)
            exp_score  = round(0.65 + ((h >> 4) % 30) / 100, 2)
            cam_rating = 0
        else:
            try:
                ph = _phash(pil_img)
            except Exception as e:
                print(f'  (pHash失敗) {p.name}: {e}')
                continue

            bgr = cv2.imread(str(p))
            if bgr is None:
                print(f'  (cv2読込失敗) {p.name}')
                continue

            hist = _lab_hist(bgr)
            n_persons, eye_score, face_rects = detector.detect(bgr)
            sharp = _sharpness_subject(bgr, face_rects)
            exp_score = _exposure_score(bgr)
            cam_rating = read_camera_rating(p)

        shots.append(Shot(
            path=p, stem=p.stem, dt=dt, phash=ph,
            hist=hist, person_count=n_persons, eye_score=eye_score,
            sharpness_raw=sharp, exposure_score=exp_score,
            camera_rating=cam_rating,
        ))

        if verbose and (i + 1) % 50 == 0:
            print(f'  {i + 1}/{len(jpeg_files)} ({time.time() - t0:.1f}s)')

    if n_skipped:
        print(f'Stage1除外: {n_skipped}枚スキップ')

    return shots


# ---- メイン ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stage 2 Step A: シーングルーピング + 技術スコアリング',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir')
    parser.add_argument('--output',                default='stage2_groups.csv')
    parser.add_argument('--xmp-dir',               default=None,
                        help='Stage1のXMP出力ディレクトリ。指定するとpick=-1のファイルをスキップする')
    parser.add_argument('--time-gap',              type=int,   default=DEFAULT_TIME_GAP_SEC)
    parser.add_argument('--phash-split',           type=int,   default=DEFAULT_PHASH_SPLIT)
    parser.add_argument('--hist-split',            type=float, default=DEFAULT_HIST_SPLIT)
    parser.add_argument('--min-visual-gap',        type=float, default=DEFAULT_MIN_VISUAL_GAP,
                        help='この秒数未満の連続ショットは pHash 分割しない')
    parser.add_argument('--solo-merge-gap',        type=float, default=DEFAULT_SOLO_MERGE_GAP,
                        help='隣接SOLOをこの秒数以内でマージ（0=無効）')
    parser.add_argument('--enable-position-bonus', action='store_true',
                        help='first/last/soloにボーナス重みを付与（旧動作）')
    parser.add_argument('--verbose',               action='store_true')
    parser.add_argument('--demo',                  action='store_true',
                        help='デモモード: 重い画像解析（MediaPipe/OpenCV）をスキップしサンプルデータを生成する')
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.exists():
        print(f'エラー: {jpeg_dir}')
        sys.exit(1)

    rejected_stems: set[str] = set()
    if args.xmp_dir:
        xmp_dir = Path(args.xmp_dir)
        if not xmp_dir.exists():
            print(f'エラー: xmp-dir が見つかりません: {xmp_dir}')
            sys.exit(1)
        rejected_stems = load_rejected_stems(xmp_dir)

    print('\n=== Aesthetic Shadowing Agent - Stage 2 Step A ===')
    print(f'対象: {jpeg_dir}')
    if args.xmp_dir:
        print(f'XMPディレクトリ: {args.xmp_dir}')
    print(f'時刻ギャップ: {args.time_gap}s  '
          f'pHash閾値: {args.phash_split}  '
          f'ヒスト相関閾値: {args.hist_split}  '
          f'min_visual_gap: {args.min_visual_gap}s  '
          f'solo_merge_gap: {args.solo_merge_gap}s  '
          f'position_bonus: {args.enable_position_bonus}')
    print()

    if args.demo:
        print('[デモモード] MediaPipe/OpenCV 解析をスキップします')
        detector = None
    else:
        detector = PersonDetector()

    t_start = time.time()
    shots = load_shots(jpeg_dir, detector, args.verbose, rejected_stems, demo=args.demo)
    t_load = time.time() - t_start
    print(f'{len(shots)} ファイル読み込み完了 ({t_load:.1f}s / '
          f'{t_load / len(shots) * 100:.1f}s per 100枚)')

    # 時刻順ソート（EXIF なしは末尾）
    shots.sort(key=lambda s: (s.dt is None, s.dt or datetime.min, s.stem))

    assign_groups(shots, args.time_gap, args.phash_split, args.hist_split,
                  args.min_visual_gap)
    merge_solo_groups(shots, args.solo_merge_gap)
    assign_positions(shots, enable_bonus=args.enable_position_bonus)
    compute_technical_scores(shots)
    assign_near_rated(shots)

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
        'position', 'person_count', 'eye_score',
        'sharpness_score', 'exposure_score', 'technical_score',
        'bonus_weight', 'camera_rating', 'near_rated', 'phash',
    ]
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in sorted(shots, key=lambda s: (s.group_id, s.dt or datetime.min, s.stem)):
            writer.writerow({
                'file':            s.stem + '.JPG',
                'datetime':        s.dt.isoformat() if s.dt else '',
                'group_id':        s.group_id,
                'group_size':      group_sizes[s.group_id],
                'position':        s.position,
                'person_count':    s.person_count,
                'eye_score':       f'{s.eye_score:.4f}' if s.eye_score is not None else '',
                'sharpness_score': f'{s.sharpness_score:.4f}',
                'exposure_score':  f'{s.exposure_score:.4f}',
                'technical_score': f'{s.technical_score:.4f}',
                'bonus_weight':    s.bonus_weight,
                'camera_rating':   s.camera_rating,
                'near_rated':      s.near_rated,
                'phash':           str(s.phash),
            })

    print()
    print(f'CSV: {output_path}')


if __name__ == '__main__':
    main()
