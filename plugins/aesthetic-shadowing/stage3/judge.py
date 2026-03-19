#!/usr/bin/env python3
"""
Stage 3: 代表カット30枚に人間が1〜5をつけて審美眼を学習

設計:
  Stage2 CSVから代表カットを自動選定し、1枚ずつ表示して
  ユーザーに1〜5のレーティングを入力してもらう。
  結果は rated_samples.json に保存し、Stage4のスタイルルール学習に使う。

選定ロジック:
  - グループ数 >= samples: 各グループから1枚（technical_score最高）
  - グループ数 < samples: 大きいグループから追加サンプリングで調整
  - technical_scoreがない場合はposition=="first"のカットで代替

使い方:
  python judge.py <jpeg_dir> --csv <stage2_groups.csv> [オプション]

例:
  python judge.py /path/to/S2_JPEG/ --csv /path/to/stage2_groups.csv
  python judge.py /path/to/S2_JPEG/ --csv stage2_groups.csv --session 学校PR撮影2026 --samples 30
"""

import argparse
import csv as _csv
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ---- 定数 ----

LEARNING_WEIGHTS = {
    5: 1.0,   # 確実な採用例
    4: 0.7,
    3: 0.2,   # あいまい、学習への影響小
    2: 0.7,
    1: 1.0,   # 確実な不採用例
}


# ---- データ構造 ----

@dataclass
class ShotRecord:
    """1ショットの情報（Stage2 CSV から）。"""
    file: str
    group_id: int
    group_size: int
    position: str           # first / last / middle / solo
    technical_score: float  # 0.0〜1.0（新CSVのみ）
    has_technical: bool     # technical_scoreフィールドが存在したか


@dataclass
class SampleEntry:
    """レーティング対象の1枚。"""
    file: str
    group_id: int
    technical_score: float
    human_rating: int | None = None   # None = 未評価
    skipped: bool = False

    def to_dict(self) -> dict:
        weight = 0.0
        if not self.skipped and self.human_rating is not None:
            weight = LEARNING_WEIGHTS.get(self.human_rating, 0.0)
        return {
            "file":            self.file,
            "group_id":        self.group_id,
            "technical_score": self.technical_score,
            "human_rating":    self.human_rating,
            "learning_weight": weight,
            "skipped":         self.skipped,
        }


# ---- Stage2 CSV 読み込み ----

def load_shots(csv_path: Path) -> dict[int, list[ShotRecord]]:
    """stage2_groups.csv を全行読み込み、グループID → ショットリスト を返す。"""
    by_group: dict[int, list[ShotRecord]] = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        has_technical = "technical_score" in fieldnames

        for row in reader:
            gid = int(row["group_id"])
            tech = float(row["technical_score"]) if has_technical else 0.0
            by_group.setdefault(gid, []).append(ShotRecord(
                file=row["file"],
                group_id=gid,
                group_size=int(row.get("group_size", 1)),
                position=row.get("position", ""),
                technical_score=tech,
                has_technical=has_technical,
            ))

    # 各グループを technical_score 降順（なければ position=="first" 優先）でソート
    for members in by_group.values():
        if members[0].has_technical:
            members.sort(key=lambda s: s.technical_score, reverse=True)
        else:
            members.sort(key=lambda s: (0 if s.position == "first" else 1, s.file))

    return by_group


def _best_shot(members: list[ShotRecord]) -> ShotRecord:
    """グループの代表1枚を返す。technical_score最高 or position==first。"""
    return members[0]  # load_shots でソート済み


# ---- 代表カット選定 ----

def select_samples(
    by_group: dict[int, list[ShotRecord]],
    n_samples: int,
    jpeg_dir: Path,
) -> list[SampleEntry]:
    """代表カットを n_samples 枚選定する。"""
    # Step 1: 各グループから1枚（代表）
    groups_sorted = sorted(by_group.items())  # group_id 昇順
    primary: list[SampleEntry] = []
    for gid, members in groups_sorted:
        shot = _best_shot(members)
        path = jpeg_dir / shot.file
        if path.exists():
            primary.append(SampleEntry(
                file=shot.file,
                group_id=gid,
                technical_score=shot.technical_score,
            ))

    if len(primary) >= n_samples:
        # グループ数が多い場合: n_samples 枚均等にサンプリング
        step = len(primary) / n_samples
        selected = [primary[int(i * step)] for i in range(n_samples)]
        return selected

    # グループ数が少ない場合: 大きいグループから追加サンプリング
    selected = list(primary)
    already = {e.file for e in selected}

    # 追加候補: 大グループの2枚目以降を group_size 降順でリスト化
    candidates: list[SampleEntry] = []
    for gid, members in sorted(by_group.items(), key=lambda x: -len(x[1])):
        for shot in members[1:]:  # 2枚目以降
            path = jpeg_dir / shot.file
            if path.exists() and shot.file not in already:
                candidates.append(SampleEntry(
                    file=shot.file,
                    group_id=gid,
                    technical_score=shot.technical_score,
                ))
                already.add(shot.file)

    # technical_score 降順で追加
    candidates.sort(key=lambda e: e.technical_score, reverse=True)
    need = n_samples - len(selected)
    selected += candidates[:need]

    # group_id 昇順に並べ直す
    selected.sort(key=lambda e: (e.group_id, -e.technical_score))
    return selected


# ---- Preview 表示 ----

def open_preview(path: Path) -> None:
    try:
        subprocess.Popen(
            ["open", "-a", "Preview", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
    except Exception:
        pass


# ---- セッション保存・読み込み ----

def load_session(output_path: Path) -> dict | None:
    if output_path.exists():
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_session(
    output_path: Path,
    session_name: str,
    samples: list[SampleEntry],
    created_at: str,
) -> None:
    completed = sum(
        1 for s in samples if s.human_rating is not None or s.skipped
    )
    data = {
        "session_name": session_name,
        "created_at":   created_at,
        "total_samples": len(samples),
        "completed":    completed,
        "samples":      [s.to_dict() for s in samples],
    }
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---- CLIレーティングセッション ----

def run_rating_session(
    samples: list[SampleEntry],
    jpeg_dir: Path,
    output_path: Path,
    session_name: str,
    created_at: str,
) -> None:
    total = len(samples)
    # 未評価インデックスを特定（再開対応）
    start_idx = next(
        (i for i, s in enumerate(samples)
         if s.human_rating is None and not s.skipped),
        None,
    )

    if start_idx is None:
        print("すべてのサンプルが評価済みです。")
        return

    if start_idx > 0:
        print(f"前回の続きから再開します（{start_idx}/{total} 完了済み）")

    print(f"\n=== Aesthetic Shadowing Agent - Stage 3: 審美眼サンプリング ===")
    if session_name:
        print(f"セッション: {session_name}")
    print()
    print("レーティング凡例:")
    print("  ★5 = 絶対に使う  ★4 = 採用  ★3 = 保留")
    print("  ★2 = 不採用      ★1 = 絶対に使わない")
    print("  s  = スキップ     x  = 中断して保存")
    print()

    for i in range(start_idx, total):
        entry = samples[i]
        path = jpeg_dir / entry.file

        print(f"[{i + 1}/{total}] グループ{entry.group_id} / "
              f"technical: {entry.technical_score:.2f}")
        print(f"  ファイル: {entry.file}")

        open_preview(path)

        while True:
            raw = input("> ").strip().lower()
            if raw in ("1", "2", "3", "4", "5"):
                entry.human_rating = int(raw)
                entry.skipped = False
                break
            elif raw in ("s", "skip"):
                entry.skipped = True
                entry.human_rating = None
                print("  → スキップ")
                break
            elif raw in ("x", "exit"):
                print("\nセッションを中断します。途中まで保存しました。")
                save_session(output_path, session_name, samples, created_at)
                print(f"  {output_path}")
                return
            else:
                print("  1〜5、s（スキップ）、x（中断）で入力してください。")

        if entry.human_rating is not None:
            stars = "★" * entry.human_rating + "☆" * (5 - entry.human_rating)
            print(f"  → {stars} ({entry.human_rating})")

        save_session(output_path, session_name, samples, created_at)

    completed = sum(1 for s in samples if s.human_rating is not None or s.skipped)
    rated = sum(1 for s in samples if s.human_rating is not None)
    print(f"\n✅ 完了: {rated}枚を評価 / {completed - rated}枚スキップ")
    print(f"   結果: {output_path}")


# ---- エントリポイント ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 3: 代表カット30枚に人間が1〜5をつけて審美眼を学習",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("jpeg_dir",
                        help="JPEG画像が入ったディレクトリ")
    parser.add_argument("--csv",      required=True,
                        help="Stage2出力のCSV（stage2_groups.csv）")
    parser.add_argument("--session",  default="",
                        help="セッション名（例: 学校PR撮影2026）")
    parser.add_argument("--output",   default="rated_samples.json",
                        help="出力JSONファイル名")
    parser.add_argument("--samples",  type=int, default=30,
                        help="選定する代表カット枚数")
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.exists():
        print(f"エラー: JPEG ディレクトリが見つかりません: {jpeg_dir}", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = jpeg_dir.parent / csv_path
    if not csv_path.exists():
        print(f"エラー: CSV が見つかりません: {csv_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = jpeg_dir.parent / output_path

    # CSV 読み込み
    by_group = load_shots(csv_path)
    total_shots = sum(len(m) for m in by_group.values())
    print(f"CSV 読み込み: {total_shots}枚 / {len(by_group)}グループ")

    # セッション復元 or 新規選定
    session_data = load_session(output_path)
    session_name = args.session

    if session_data is not None:
        # 前回セッション復元
        if not session_name:
            session_name = session_data.get("session_name", "")
        created_at = session_data.get("created_at", datetime.now().isoformat())
        raw_samples = session_data.get("samples", [])
        samples = [
            SampleEntry(
                file=s["file"],
                group_id=s["group_id"],
                technical_score=s["technical_score"],
                human_rating=s["human_rating"],
                skipped=s.get("skipped", False),
            )
            for s in raw_samples
        ]
        print(f"前回セッション復元: {session_data.get('completed', 0)}/"
              f"{session_data.get('total_samples', 0)} 完了")
    else:
        # 新規選定
        created_at = datetime.now().isoformat()
        samples = select_samples(by_group, args.samples, jpeg_dir)
        print(f"代表カット選定: {len(samples)}枚 / {len(by_group)}グループから")

        if not samples:
            print("エラー: 有効なJPEGが見つかりません。", file=sys.stderr)
            sys.exit(1)

        # 選定結果を先に保存（中断時でも再開できるよう）
        save_session(output_path, session_name, samples, created_at)

    run_rating_session(
        samples=samples,
        jpeg_dir=jpeg_dir,
        output_path=output_path,
        session_name=session_name,
        created_at=created_at,
    )


if __name__ == "__main__":
    main()
