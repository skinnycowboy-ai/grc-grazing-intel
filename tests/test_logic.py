from grc_pipeline.logic.days_remaining import (
    compute_days_remaining,
    daily_consumption_kg,
    recommend_move_date,
)


def test_daily_consumption():
    assert daily_consumption_kg(120, 11.5) == 1380.0


def test_days_remaining():
    assert compute_days_remaining(available_forage_kg=1380.0, daily_consumption_kg=1380.0) == 1.0


def test_move_date_floor():
    assert recommend_move_date("2024-03-01", 10.9) == "2024-03-11"
    assert recommend_move_date("2024-03-01", 0.1) == "2024-03-01"
