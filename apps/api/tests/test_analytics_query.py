"""Deterministic Ask-AI question parser."""

from datetime import date

from parlio_api.analytics_query import parse_question

TODAY = date(2026, 9, 11)  # Friday


def test_this_month_vs_last_month() -> None:
    q = parse_question("this month vs last month", TODAY)
    assert (q.period.start, q.period.end) == (date(2026, 9, 1), TODAY)
    assert q.compare and (q.compare.start, q.compare.end) == (date(2026, 8, 1), date(2026, 8, 31))
    assert q.source == "rules" and "vs" in q.interpretation


def test_named_months_and_quarters() -> None:
    q = parse_question("November 2024 for P2", TODAY)
    assert (q.period.start, q.period.end) == (date(2024, 11, 1), date(2024, 11, 30))
    q = parse_question("Q3 vs Q4 2024", TODAY)
    assert (q.period.start, q.period.end) == (date(2026, 7, 1), date(2026, 9, 30))
    assert q.compare and (q.compare.start, q.compare.end) == (date(2024, 10, 1), date(2024, 12, 31))
    # a month later than today without a year means last year's
    assert parse_question("december", TODAY).period.start == date(2025, 12, 1)


def test_year_over_year_and_relative_ranges() -> None:
    q = parse_question("this year vs last year", TODAY)
    assert q.period.start == date(2026, 1, 1) and q.period.end == TODAY
    assert q.compare and q.compare.end == date(2025, 12, 31)
    q = parse_question("last 14 days", TODAY)
    assert q.period.start == date(2026, 8, 29) and q.compare is None
    q = parse_question("last week", TODAY)
    assert (q.period.start, q.period.end) == (date(2026, 8, 31), date(2026, 9, 6))


def test_filters_apply_to_both_sides() -> None:
    q = parse_question("weekend calls in August vs July", TODAY)
    assert q.period.days == "weekends" and q.compare and q.compare.days == "weekends"
    q = parse_question("after hours calls last month", TODAY)
    assert q.period.hours == "after" and q.compare is None
    q = parse_question("business hours vs after hours this month", TODAY)
    assert q.period.hours == "business" and q.compare and q.compare.hours == "after"
    assert (q.compare.start, q.compare.end) == (q.period.start, q.period.end)


def test_unknown_text_defaults_to_30_days() -> None:
    q = parse_question("how are we doing", TODAY)
    assert q.period.end == TODAY and (TODAY - q.period.start).days == 29
