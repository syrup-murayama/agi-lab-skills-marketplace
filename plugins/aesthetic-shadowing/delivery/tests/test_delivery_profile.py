from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "delivery_profile.py"
SPEC = importlib.util.spec_from_file_location("delivery_profile", MODULE_PATH)
delivery_profile = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(delivery_profile)


def test_build_profile_sets_context_and_empty_keywords() -> None:
    profile = delivery_profile.build_profile("wedding reception with candle light")

    assert profile == {
        "clip_query": "wedding reception with candle light",
        "high_keywords": [],
        "low_keywords": [],
    }


def test_write_output_exits_when_csv_does_not_exist(tmp_path: Path) -> None:
    output_path = tmp_path / "output.csv"

    with pytest.raises(SystemExit) as excinfo:
        delivery_profile.write_output(str(tmp_path / "missing.csv"), output_path)

    assert excinfo.value.code == 1


def test_write_output_exits_when_csv_is_empty(tmp_path: Path) -> None:
    scores_csv = tmp_path / "scores.csv"
    scores_csv.write_text("", encoding="utf-8")
    output_path = tmp_path / "output.csv"

    with pytest.raises(SystemExit) as excinfo:
        delivery_profile.write_output(str(scores_csv), output_path)

    assert excinfo.value.code == 1


def test_write_output_writes_normalized_csv(tmp_path: Path) -> None:
    scores_csv = tmp_path / "scores.csv"
    scores_csv.write_text(
        "filename,star_rating,composite_score\n"
        "a.jpg,5,0.95\n"
        "b.jpg,,0.20\n"
        "c.jpg,not-an-int,not-a-float\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "nested" / "output.csv"

    delivery_profile.write_output(str(scores_csv), output_path)

    with output_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows == [
        {"filename": "a.jpg", "star_rating": "5", "composite_score": "0.95"},
        {"filename": "b.jpg", "star_rating": "0", "composite_score": "0.2"},
        {"filename": "c.jpg", "star_rating": "0", "composite_score": "0.0"},
    ]
