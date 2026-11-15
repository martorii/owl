from owl.application.ports import ExtractionOutcome, GroupExtractionError


def test_group_extraction_error_carries_group_and_reason() -> None:
    error = GroupExtractionError("financials", "retries exhausted")
    assert error.group == "financials"
    assert error.reason == "retries exhausted"
    assert "financials" in str(error)


def test_extraction_outcome_defaults_to_no_warnings() -> None:
    assert ExtractionOutcome(()).warnings == ()
