from pathlib import Path

from app import classify_event, normalized_event, parse_ical


FIXTURE = Path(__file__).parents[2] / "Engineering Mechanics-Statics .ics"


def test_sample_feed_parses_and_filters_expected_event_types():
    events = parse_ical(FIXTURE.read_text())
    assert len(events) == 157
    by_title = {event["summary"].strip(): event for event in events}
    assert classify_event(by_title["Dot Product"]) == "review"
    assert classify_event(by_title["HW #5 Upload - 3D Equilibrium & Projection Lines"]) == "include"
    assert classify_event(by_title["HW #5 Report - 3D Equilibrium, Projection Lines"]) == "include"
    assert classify_event(by_title["No Class :)"]) == "exclude"
    assert classify_event(by_title["Corrections"]) == "exclude"
    assert classify_event(by_title["Student Ratings"]) == "exclude"


def test_dtend_is_the_due_date_and_is_not_subtracted():
    event = next(event for event in parse_ical(FIXTURE.read_text()) if event["summary"].strip() == "RQuiz #1 1.All - Vectors")
    normalized = normalized_event(event)
    assert normalized["start_at"] == "2026-09-02"
    assert normalized["due_at"] == "2026-09-05"
    assert normalized["all_day"] is True


def test_escaped_and_folded_text_is_normalized():
    event = next(event for event in parse_ical(FIXTURE.read_text()) if event["summary"].startswith("HW #32"))
    assert "Area Moments" in event["summary"]
    assert "Upload a scan" in event["description"]

