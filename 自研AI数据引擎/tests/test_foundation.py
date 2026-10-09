from datetime import date

from cnts_engine.foundation import normalize_time_phrase
from cnts_engine.recommendations import draft_recommendation


def test_last_week_uses_calendar_week() -> None:
    assert normalize_time_phrase("上周", date(2026, 10, 3)) == (
        date(2026, 9, 21),
        date(2026, 9, 27),
    )


def test_recommendation_never_auto_activates() -> None:
    recommendation = draft_recommendation(
        field="company",
        role="common",
        reason="稳定共有组织维度",
        confidence=0.9,
        source="combined",
    )
    assert recommendation.status == "pending_review"
    recommendation.confirm()
    assert recommendation.status == "confirmed"
