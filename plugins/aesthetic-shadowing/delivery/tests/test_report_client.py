from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "report_client.py"
SPEC = importlib.util.spec_from_file_location("report_client", MODULE_PATH)
report_client = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
pil_stub = types.ModuleType("PIL")
pil_stub.Image = object()
sys.modules.setdefault("PIL", pil_stub)
SPEC.loader.exec_module(report_client)


def test_load_scores_reads_valid_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text(
        "filename,star_rating,composite_score\n"
        "a.jpg,5,0.98\n"
        "b.jpg,2,0.12\n",
        encoding="utf-8",
    )

    scores = report_client.load_scores(str(csv_path))

    assert scores == {
        "a.jpg": {"star_rating": 5, "composite_score": 0.98},
        "b.jpg": {"star_rating": 2, "composite_score": 0.12},
    }


def test_load_scores_falls_back_when_star_rating_is_empty(tmp_path: Path) -> None:
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text(
        "filename,star_rating,composite_score\n"
        "a.jpg,,1.5\n",
        encoding="utf-8",
    )

    scores = report_client.load_scores(str(csv_path))

    assert scores["a.jpg"]["star_rating"] == 0
    assert scores["a.jpg"]["composite_score"] == 1.5


def test_load_scores_falls_back_when_composite_score_is_invalid(tmp_path: Path) -> None:
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text(
        "filename,star_rating,composite_score\n"
        "a.jpg,4,not-a-number\n",
        encoding="utf-8",
    )

    scores = report_client.load_scores(str(csv_path))

    assert scores["a.jpg"] == {"star_rating": 0, "composite_score": 0.0}


def test_load_scores_returns_empty_dict_when_file_is_missing(tmp_path: Path) -> None:
    scores = report_client.load_scores(str(tmp_path / "missing.csv"))

    assert scores == {}


def test_load_scores_returns_empty_dict_when_path_is_empty() -> None:
    assert report_client.load_scores("") == {}


def test_assign_groups_keeps_shots_within_five_minutes_in_same_group() -> None:
    photos = [
        {"filename": "a.jpg", "datetime_str": "2024:01:01 10:00:00"},
        {"filename": "b.jpg", "datetime_str": "2024:01:01 10:05:00"},
    ]

    report_client.assign_groups(photos)

    assert photos[0]["group_id"] == photos[1]["group_id"]


def test_assign_groups_splits_shots_more_than_five_minutes_apart() -> None:
    photos = [
        {"filename": "a.jpg", "datetime_str": "2024:01:01 10:00:00"},
        {"filename": "b.jpg", "datetime_str": "2024:01:01 10:05:01"},
    ]

    report_client.assign_groups(photos)

    assert photos[0]["group_id"] != photos[1]["group_id"]


def test_assign_groups_assigns_individual_groups_when_datetime_is_missing() -> None:
    photos = [
        {"filename": "a.jpg", "datetime_str": "2024:01:01 10:00:00"},
        {"filename": "b.jpg"},
        {"filename": "c.jpg", "datetime_str": ""},
    ]

    report_client.assign_groups(photos)

    group_ids = [photo["group_id"] for photo in photos]
    assert all(isinstance(group_id, int) for group_id in group_ids)
    assert len(set(group_ids)) == 3


def test_assign_groups_accepts_empty_photo_list() -> None:
    photos: list[dict] = []

    report_client.assign_groups(photos)

    assert photos == []


def test_sha256_hex_returns_expected_hash() -> None:
    expected = hashlib.sha256(b"hello").hexdigest()

    assert report_client.sha256_hex("hello") == expected
