#!/usr/bin/env python3
"""
Stage 3: 代表カット30枚に人間が1〜5をつけて審美眼を学習（ブラウザUI版）

設計:
  Stage2 CSVから代表カットを自動選定し、ブラウザUIで1枚ずつ表示して
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
import mimetypes
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse


# ---- 定数 ----

LEARNING_WEIGHTS = {
    5: 1.0,   # 確実な採用例
    4: 0.7,
    3: 0.2,   # あいまい、学習への影響小
    2: 0.7,
    1: 1.0,   # 確実な不採用例
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aesthetic Shadowing — Stage 3</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #1a1a1a;
    color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  #header {
    width: 100%;
    max-width: 960px;
    padding: 16px 20px 8px;
  }
  #session-name {
    font-size: 13px;
    color: #888;
    margin-bottom: 8px;
  }
  #progress-bar-wrap {
    background: #333;
    border-radius: 4px;
    height: 8px;
    width: 100%;
  }
  #progress-bar {
    background: #4a9eff;
    height: 8px;
    border-radius: 4px;
    transition: width 0.3s ease;
  }
  #progress-text {
    font-size: 13px;
    color: #aaa;
    margin-top: 6px;
    text-align: right;
  }
  #image-wrap {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px 20px;
    width: 100%;
    max-width: 960px;
  }
  #photo {
    max-width: 100%;
    max-height: calc(100vh - 220px);
    border-radius: 6px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.6);
    object-fit: contain;
  }
  #meta {
    font-size: 12px;
    color: #666;
    text-align: center;
    margin-top: 8px;
  }
  #controls {
    width: 100%;
    max-width: 960px;
    padding: 12px 20px 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  #rating-buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: center;
  }
  .rating-btn {
    background: #2a2a2a;
    border: 2px solid #444;
    color: #e0e0e0;
    font-size: 20px;
    padding: 12px 20px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s ease;
    min-width: 70px;
    text-align: center;
  }
  .rating-btn:hover { background: #3a3a3a; border-color: #4a9eff; }
  .rating-btn[data-rating="5"]:hover { border-color: #ffd700; }
  .rating-btn[data-rating="4"]:hover { border-color: #ffa500; }
  .rating-btn[data-rating="3"]:hover { border-color: #888; }
  .rating-btn[data-rating="2"]:hover { border-color: #ff7777; }
  .rating-btn[data-rating="1"]:hover { border-color: #ff3333; }
  .rating-btn .key-hint {
    display: block;
    font-size: 11px;
    color: #666;
    margin-top: 2px;
  }
  .rating-btn .rating-label {
    display: block;
    font-size: 0.7rem;
    color: #888;
    margin-top: 4px;
    white-space: nowrap;
  }
  #skip-btn {
    background: transparent;
    border: 1px solid #444;
    color: #888;
    font-size: 14px;
    padding: 8px 24px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  #skip-btn:hover { border-color: #888; color: #ccc; }
  #shortcut-hint {
    font-size: 12px;
    color: #555;
  }
  /* 完了画面 */
  #done-screen {
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    gap: 20px;
    text-align: center;
    padding: 40px;
  }
  #done-screen h1 { font-size: 36px; }
  #done-screen p { color: #aaa; font-size: 16px; }
  #done-screen .stats { color: #4a9eff; font-size: 18px; }
  /* フラッシュ効果 */
  #flash {
    position: fixed;
    inset: 0;
    background: white;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.08s ease;
  }
</style>
</head>
<body>
<div id="flash"></div>

<div id="header">
  <div id="session-name"></div>
  <div id="progress-bar-wrap"><div id="progress-bar" style="width:0%"></div></div>
  <div id="progress-text">0 / 0</div>
</div>

<div id="image-wrap">
  <div>
    <img id="photo" src="" alt="Loading...">
    <div id="meta"></div>
  </div>
</div>

<div id="controls">
  <div id="rating-buttons">
    <button class="rating-btn" data-rating="1">
      ★☆☆☆☆
      <span class="key-hint">[ 1 ]</span>
      <span class="rating-label">絶対に使わない</span>
    </button>
    <button class="rating-btn" data-rating="2">
      ★★☆☆☆
      <span class="key-hint">[ 2 ]</span>
      <span class="rating-label">不採用</span>
    </button>
    <button class="rating-btn" data-rating="3">
      ★★★☆☆
      <span class="key-hint">[ 3 ]</span>
      <span class="rating-label">保留</span>
    </button>
    <button class="rating-btn" data-rating="4">
      ★★★★☆
      <span class="key-hint">[ 4 ]</span>
      <span class="rating-label">採用</span>
    </button>
    <button class="rating-btn" data-rating="5">
      ★★★★★
      <span class="key-hint">[ 5 ]</span>
      <span class="rating-label">絶対に使う</span>
    </button>
  </div>
  <button id="skip-btn">スキップ &nbsp;[ S ]</button>
  <div id="shortcut-hint">キーボード: 1〜5でレーティング、Sでスキップ</div>
</div>

<div id="done-screen">
  <h1>✅ 完了</h1>
  <p class="stats" id="done-stats"></p>
  <p>結果を保存しました。このタブを閉じてください。</p>
</div>

<script>
const STATE = {
  samples: [],
  currentIndex: 0,
  sessionName: "",
  busy: false,
};

async function loadStatus() {
  const res = await fetch("/status");
  const data = await res.json();
  STATE.samples = data.samples;
  STATE.sessionName = data.session_name;
  // 最初の未評価インデックスを探す
  STATE.currentIndex = STATE.samples.findIndex(
    s => s.human_rating === null && !s.skipped
  );
  if (STATE.currentIndex === -1) STATE.currentIndex = STATE.samples.length;
  render();
}

function render() {
  const total = STATE.samples.length;
  const completed = STATE.samples.filter(
    s => s.human_rating !== null || s.skipped
  ).length;

  // セッション名
  const sessionEl = document.getElementById("session-name");
  sessionEl.textContent = STATE.sessionName ? `セッション: ${STATE.sessionName}` : "";

  // 進捗
  const pct = total > 0 ? (completed / total) * 100 : 0;
  document.getElementById("progress-bar").style.width = pct + "%";
  document.getElementById("progress-text").textContent = `${completed} / ${total}`;

  if (STATE.currentIndex >= total) {
    showDone(completed, total);
    return;
  }

  const entry = STATE.samples[STATE.currentIndex];
  document.getElementById("photo").src = `/image/${encodeURIComponent(entry.file)}`;
  document.getElementById("meta").textContent =
    `グループ${entry.group_id}  /  technical: ${entry.technical_score.toFixed(2)}  /  ${entry.file}`;
}

function showDone(rated, total) {
  document.getElementById("header").style.display = "none";
  document.getElementById("image-wrap").style.display = "none";
  document.getElementById("controls").style.display = "none";
  const doneEl = document.getElementById("done-screen");
  doneEl.style.display = "flex";
  document.getElementById("done-stats").textContent = `${rated}枚 / ${total}枚 を評価しました`;
}

function flash() {
  const el = document.getElementById("flash");
  el.style.opacity = "0.25";
  setTimeout(() => { el.style.opacity = "0"; }, 80);
}

async function submitRating(rating) {
  if (STATE.busy || STATE.currentIndex >= STATE.samples.length) return;
  STATE.busy = true;
  flash();
  try {
    await fetch("/rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: STATE.currentIndex, rating }),
    });
    STATE.samples[STATE.currentIndex].human_rating = rating;
    STATE.samples[STATE.currentIndex].skipped = false;
    // 次の未評価へ
    const next = STATE.samples.findIndex(
      (s, i) => i > STATE.currentIndex && s.human_rating === null && !s.skipped
    );
    STATE.currentIndex = next === -1 ? STATE.samples.length : next;
    render();
  } finally {
    STATE.busy = false;
  }
}

async function submitSkip() {
  if (STATE.busy || STATE.currentIndex >= STATE.samples.length) return;
  STATE.busy = true;
  try {
    await fetch("/skip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: STATE.currentIndex }),
    });
    STATE.samples[STATE.currentIndex].skipped = true;
    STATE.samples[STATE.currentIndex].human_rating = null;
    const next = STATE.samples.findIndex(
      (s, i) => i > STATE.currentIndex && s.human_rating === null && !s.skipped
    );
    STATE.currentIndex = next === -1 ? STATE.samples.length : next;
    render();
  } finally {
    STATE.busy = false;
  }
}

// ボタンイベント
document.querySelectorAll(".rating-btn").forEach(btn => {
  btn.addEventListener("click", () => submitRating(parseInt(btn.dataset.rating)));
});
document.getElementById("skip-btn").addEventListener("click", submitSkip);

// キーボードショートカット
document.addEventListener("keydown", e => {
  if (e.repeat) return;
  if (["1","2","3","4","5"].includes(e.key)) {
    submitRating(parseInt(e.key));
  } else if (e.key.toLowerCase() === "s") {
    submitSkip();
  }
});

// 初期化
loadStatus();
</script>
</body>
</html>
"""


# ---- データ構造 ----

@dataclass
class ShotRecord:
    """1ショットの情報（Stage2 CSV から）。"""
    file: str
    group_id: int
    group_size: int
    position: str           # first / last / middle / solo
    technical_score: float  # 0.0〜1.0（新CSVのみ）
    sharpness_score: float  # 0.0〜1.0 グループ内相対シャープネス
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
            sharp = float(row["sharpness_score"]) if "sharpness_score" in fieldnames else 1.0
            by_group.setdefault(gid, []).append(ShotRecord(
                file=row["file"],
                group_id=gid,
                group_size=int(row.get("group_size", 1)),
                position=row.get("position", ""),
                technical_score=tech,
                sharpness_score=sharp,
                has_technical=has_technical,
            ))

    # 各グループを position 優先でソート: first → solo → last → middle
    _pos_order = {"first": 0, "solo": 1, "last": 2, "middle": 3}
    for members in by_group.values():
        members.sort(key=lambda s: (_pos_order.get(s.position, 3), s.file))

    return by_group


# ブレ絶対失敗の閾値（グループ内相対スコアがこれ未満 = 明らかな失敗カット）
_BLUR_FAIL_THRESHOLD = 0.1


def _best_shot(members: list[ShotRecord]) -> ShotRecord:
    """グループの代表1枚を返す。

    選定ルール:
    1. sharpness_score が極端に低い（< 0.1）カットを候補から除外（代替がある場合のみ）
    2. 残った候補から position == 'first' を優先（load_shots でソート済み）
    3. すべて失敗の場合は先頭にフォールバック
    """
    candidates = [m for m in members if m.sharpness_score >= _BLUR_FAIL_THRESHOLD]
    if not candidates:
        candidates = members  # 全カット失敗 → フォールバック
    return candidates[0]  # load_shots で position='first' 優先にソート済み


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

    # group_id 昇順に並べ直す（多様性を保つためスコアではなくファイル順）
    selected.sort(key=lambda e: (e.group_id, e.file))
    return selected


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


# ---- HTTP サーバー ----

class _AppState:
    """スレッド間で共有するアプリ状態。"""
    def __init__(
        self,
        samples: list[SampleEntry],
        jpeg_dir: Path,
        output_path: Path,
        session_name: str,
        created_at: str,
    ):
        self.samples = samples
        self.jpeg_dir = jpeg_dir
        self.output_path = output_path
        self.session_name = session_name
        self.created_at = created_at
        self.lock = threading.Lock()
        self.done_event = threading.Event()

    def is_complete(self) -> bool:
        return all(
            s.human_rating is not None or s.skipped
            for s in self.samples
        )

    def save(self) -> None:
        save_session(
            self.output_path,
            self.session_name,
            self.samples,
            self.created_at,
        )


def _make_handler(app: _AppState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # アクセスログを抑制

        def _send_json(self, data: dict, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/":
                self._send_html(HTML_TEMPLATE)

            elif path.startswith("/image/"):
                filename = path[len("/image/"):]
                # URLデコード
                from urllib.parse import unquote
                filename = unquote(filename)
                img_path = app.jpeg_dir / filename
                if not img_path.exists():
                    self.send_response(404)
                    self.end_headers()
                    return
                ctype, _ = mimetypes.guess_type(str(img_path))
                if not ctype:
                    ctype = "image/jpeg"
                data = img_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            elif path == "/status":
                with app.lock:
                    self._send_json({
                        "session_name": app.session_name,
                        "total": len(app.samples),
                        "completed": sum(
                            1 for s in app.samples
                            if s.human_rating is not None or s.skipped
                        ),
                        "samples": [s.to_dict() for s in app.samples],
                    })

            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/rate":
                body = self._read_body()
                idx = body.get("index")
                rating = body.get("rating")
                if (
                    idx is None
                    or not isinstance(idx, int)
                    or rating not in (1, 2, 3, 4, 5)
                    or idx < 0
                    or idx >= len(app.samples)
                ):
                    self._send_json({"error": "invalid"}, 400)
                    return
                with app.lock:
                    app.samples[idx].human_rating = rating
                    app.samples[idx].skipped = False
                    app.save()
                    complete = app.is_complete()
                self._send_json({"ok": True})
                if complete:
                    # 少し待ってからシャットダウン（ブラウザに完了画面を表示する時間）
                    threading.Timer(1.5, app.done_event.set).start()

            elif path == "/skip":
                body = self._read_body()
                idx = body.get("index")
                if (
                    idx is None
                    or not isinstance(idx, int)
                    or idx < 0
                    or idx >= len(app.samples)
                ):
                    self._send_json({"error": "invalid"}, 400)
                    return
                with app.lock:
                    app.samples[idx].skipped = True
                    app.samples[idx].human_rating = None
                    app.save()
                    complete = app.is_complete()
                self._send_json({"ok": True})
                if complete:
                    threading.Timer(1.5, app.done_event.set).start()

            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def _find_free_port(start: int) -> int:
    import socket
    port = start
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                port += 1


def run_browser_session(
    samples: list[SampleEntry],
    jpeg_dir: Path,
    output_path: Path,
    session_name: str,
    created_at: str,
    start_port: int = 8765,
) -> None:
    app = _AppState(
        samples=samples,
        jpeg_dir=jpeg_dir,
        output_path=output_path,
        session_name=session_name,
        created_at=created_at,
    )

    # 既に全評価済みなら即終了
    if app.is_complete():
        print("すべてのサンプルが評価済みです。")
        return

    port = _find_free_port(start_port)
    server = HTTPServer(("127.0.0.1", port), _make_handler(app))

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://localhost:{port}"
    print(f"サーバー起動: {url}")
    print("ブラウザでレーティングを行ってください。完了後にサーバーが自動停止します。")
    print("中断する場合は Ctrl+C を押してください。")

    # ブラウザを開く
    try:
        subprocess.Popen(
            ["open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        print(f"ブラウザを手動で開いてください: {url}")

    # Ctrl+C で中断できるようにシグナルハンドラを設定
    def _handle_interrupt(sig, frame):
        print("\n中断しました。途中まで保存しました。")
        with app.lock:
            app.save()
        app.done_event.set()

    signal.signal(signal.SIGINT, _handle_interrupt)

    # 完了 or 中断を待つ
    app.done_event.wait()

    server.shutdown()

    # サマリー表示
    with app.lock:
        rated = sum(1 for s in app.samples if s.human_rating is not None)
        skipped = sum(1 for s in app.samples if s.skipped)
        total = len(app.samples)

    print(f"\n完了: {rated}枚を評価 / {skipped}枚スキップ / 合計{total}枚")
    print(f"結果: {output_path}")


# ---- エントリポイント ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 3: 代表カット30枚に人間が1〜5をつけて審美眼を学習（ブラウザUI版）",
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
    parser.add_argument("--port",     type=int, default=8765,
                        help="HTTPサーバーのポート番号（使用中なら自動インクリメント）")
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
        completed = sum(1 for s in samples if s.human_rating is not None or s.skipped)
        print(f"前回セッション復元: {completed}/{len(samples)} 完了")
    else:
        created_at = datetime.now().isoformat()
        samples = select_samples(by_group, args.samples, jpeg_dir)
        print(f"代表カット選定: {len(samples)}枚 / {len(by_group)}グループから")

        if not samples:
            print("エラー: 有効なJPEGが見つかりません。", file=sys.stderr)
            sys.exit(1)

        # 選定結果を先に保存（中断時でも再開できるよう）
        save_session(output_path, session_name, samples, created_at)

    run_browser_session(
        samples=samples,
        jpeg_dir=jpeg_dir,
        output_path=output_path,
        session_name=session_name,
        created_at=created_at,
        start_port=args.port,
    )


if __name__ == "__main__":
    main()
