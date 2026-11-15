from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.exceptions import ModelHTTPError, ModelRateLimitError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from owl.application.ports import GroupExtractionError
from owl.domain.bundle import Document, DocumentBundle
from owl.domain.catalog import ActiveField, Applicability, FieldDefinition, FieldGroup
from owl.domain.context import ContextDocument, ExtractionContext
from owl.domain.evidence import MatchKind
from owl.domain.suggestions import WarningCode
from owl.domain.taxonomy import BusinessType, Lob
from owl.domain.values import DateOrder, FieldType, Money, ParseOptions
from owl.infrastructure.llm.extractor import LlmFieldExtractor
from owl.infrastructure.llm.model_factory import ConfiguredModel
from owl.infrastructure.llm.prompting import PROMPT_VERSION, render_system, render_user
from owl.infrastructure.llm.rate_limit import RateLimitConfig, RateLimiter
from owl.infrastructure.settings import OutputMode

DOC1 = UUID("00000000-0000-0000-0000-000000000001")
DOC2 = UUID("00000000-0000-0000-0000-000000000002")
TEXT1 = "Policy schedule.\nLimit of liability: USD 5,000,000 any one occurrence.\nDeductible 15 %."
TEXT2 = "Endorsement effective 01/03/2026.\nThe limit is amended to USD 7,500,000 per occurrence."

APPLIES = Applicability(frozenset(Lob), frozenset(BusinessType))


def field(short_id: str, name: str, type_: FieldType, **kw: Any) -> ActiveField:
    definition = FieldDefinition(name, type_, f"{name} description", APPLIES, "financials", **kw)
    return ActiveField(short_id, definition)


GROUP = FieldGroup(
    "financials",
    (
        field("F1", "limit", FieldType.MONEY),
        field("F2", "inception_date", FieldType.DATE),
        field("F3", "deductible_pct", FieldType.PERCENTAGE),
    ),
)
BUNDLE = DocumentBundle(
    (
        Document(DOC1, date(2026, 1, 10), TEXT1, "schedule.pdf"),
        Document(DOC2, date(2026, 2, 20), TEXT2, "endorsement.pdf"),
    )
)
CONTEXT = ExtractionContext(
    GROUP, tuple(ContextDocument(labelled, labelled.document.text) for labelled in BUNDLE.labelled)
)


def candidate(
    value: Any, doc: str = "D1", quote: str = "Limit of liability: USD 5,000,000", **kw: Any
) -> dict[str, Any]:
    return {
        "value": value,
        "evidence": [{"doc_id": doc, "quote": quote}],
        "rationale": "stated",
        "confidence": 0.9,
    } | kw


def answer(*entries: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"fields": [{"field_id": fid, "candidates": cands} for fid, cands in entries]}


class Script:
    """A FunctionModel that replays the given answers (or raises the given errors)."""

    def __init__(self, *steps: dict[str, Any] | Exception) -> None:
        self.steps = list(steps)
        self.requests: list[list[ModelMessage]] = []

    def configured(self, mode: OutputMode = OutputMode.TOOL) -> ConfiguredModel:
        return ConfiguredModel(FunctionModel(self._call), {}, mode)

    async def _call(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(messages)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, step)])

    def retry_texts(self) -> list[str]:
        texts: list[str] = []
        for messages in self.requests[1:]:
            last = messages[-1]
            assert isinstance(last, ModelRequest)
            texts.append("\n".join(str(getattr(p, "content", "")) for p in last.parts))
        return texts


def extractor(script: Script, **kw: Any) -> LlmFieldExtractor:
    return LlmFieldExtractor(script.configured(), **kw)


# --- happy path & canonicalisation ----------------------------------------------------------


async def test_happy_path_maps_back_to_field_names_and_document_ids() -> None:
    script = Script(answer(("F1", [candidate({"amount": 5000000, "currency": "USD"})])))
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    assert outcome.warnings == ()
    [found] = outcome.candidates
    assert found.field == "limit"
    assert found.value == Money(Decimal(5000000), "USD")
    [evidence] = found.evidence
    assert evidence.document_id == DOC1
    assert evidence.document_date == date(2026, 1, 10)
    assert evidence.filename == "schedule.pdf"
    assert evidence.match is MatchKind.EXACT
    assert found.rationale == "stated"
    assert found.confidence == 0.9
    assert len(script.requests) == 1


async def test_non_canonical_values_are_canonicalised_by_the_domain() -> None:
    script = Script(
        answer(
            ("F1", [candidate("USD 5m")]),
            ("F2", [candidate("01/03/2026", "D2", "Endorsement effective 01/03/2026")]),
            ("F3", [candidate("15 %", "D1", "Deductible 15 %")]),
        )
    )
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    values = {c.field: c.value for c in outcome.candidates}
    assert values["limit"] == Money(Decimal(5000000), "USD")
    assert values["inception_date"] == date(2026, 3, 1)  # day first by default
    assert values["deductible_pct"] == Decimal("0.15")


async def test_parse_options_are_applied() -> None:
    script = Script(
        answer(("F2", [candidate("01/03/2026", "D2", "Endorsement effective 01/03/2026")]))
    )
    options = ParseOptions(date_order=DateOrder.MONTH_FIRST)
    outcome = await extractor(script, parse_options=options).extract(GROUP, CONTEXT, BUNDLE)
    assert outcome.candidates[0].value == date(2026, 1, 3)


async def test_nothing_found_is_an_empty_outcome_not_a_failure() -> None:
    outcome = await extractor(Script(answer())).extract(GROUP, CONTEXT, BUNDLE)
    assert outcome.candidates == ()
    assert outcome.warnings == ()


async def test_fuzzy_quote_is_replaced_by_the_source_text() -> None:
    quote = "Limit of liability: USD 5,000,000 any one occurence"  # typo
    script = Script(answer(("F1", [candidate("USD 5m", quote=quote)])))
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    [evidence] = outcome.candidates[0].evidence
    assert evidence.match is MatchKind.FUZZY
    assert evidence.quote == "Limit of liability: USD 5,000,000 any one occurrence"


async def test_value_not_in_quote_is_flagged_but_kept() -> None:
    script = Script(answer(("F1", [candidate("USD 9m")])))
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    assert outcome.candidates[0].flags == ("value_not_in_quote",)


async def test_confidence_is_clamped() -> None:
    script = Script(answer(("F1", [candidate("USD 5m", confidence=7)])))
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    assert outcome.candidates[0].confidence == 1.0


# --- validation, retry, aggregated message ---------------------------------------------------

GOOD = answer(("F1", [candidate("USD 5m")]))


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        (answer(("F9", [candidate("USD 5m")])), "F9"),
        (answer(("F1", [candidate("USD 5m", doc="D9")])), "D9"),
        (answer(("F1", [candidate("USD 5m", doc="")])), "doc_id"),
        (answer(("F1", [candidate("USD 5m", quote="not in any document at all")])), "quote"),
        (answer(("F1", [candidate("lots of money")])), "lots of money"),
        (answer(("F1", [candidate("USD 5m", evidence=[])])), "evidence"),
    ],
)
async def test_invalid_output_is_retried_and_the_corrected_answer_accepted(
    bad: dict[str, Any], expected: str
) -> None:
    script = Script(bad, GOOD)
    outcome = await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    assert [c.field for c in outcome.candidates] == ["limit"]
    assert outcome.warnings == ()
    [retry] = script.retry_texts()
    assert expected in retry


async def test_one_retry_message_lists_all_problems() -> None:
    bad = answer(
        ("F9", [candidate("x")]),
        (
            "F1",
            [
                candidate("USD 5m", doc="D9"),
                candidate("nonsense"),
                candidate("USD 5m", quote="zzz zzz"),
            ],
        ),
    )
    script = Script(bad, GOOD)
    await extractor(script).extract(GROUP, CONTEXT, BUNDLE)
    [retry] = script.retry_texts()
    for needle in ("F9", "D9", "nonsense", "zzz zzz"):
        assert needle in retry


async def test_a_document_outside_the_context_is_unknown() -> None:
    narrow = ExtractionContext(GROUP, (CONTEXT.documents[1],))  # only D2 shown
    script = Script(
        answer(("F1", [candidate("USD 5m", doc="D1")])),
        answer(("F1", [candidate("USD 7.5m", "D2", "USD 7,500,000")])),
    )
    outcome = await extractor(script).extract(GROUP, narrow, BUNDLE)
    assert outcome.candidates[0].value == Money(Decimal("7500000"), "USD")
    assert "D1" in script.retry_texts()[0]


# --- retries exhausted: drop the invalid parts, keep the rest (§8.4) -------------------------


async def test_exhausted_retries_keep_the_valid_part_and_warn() -> None:
    mixed = answer(
        ("F1", [candidate("USD 5m")]),
        ("F2", [candidate("not a date", "D2", "Endorsement effective 01/03/2026")]),
    )
    script = Script(mixed, mixed, mixed)  # max_retries=2 -> three attempts
    outcome = await extractor(script, max_retries=2).extract(GROUP, CONTEXT, BUNDLE)
    assert [c.field for c in outcome.candidates] == ["limit"]
    [warning] = outcome.warnings
    assert warning.code is WarningCode.CANDIDATE_DROPPED
    assert warning.field == "inception_date"
    assert warning.group == "financials"
    assert "not a date" in warning.message
    assert len(script.requests) == 3


async def test_invalid_evidence_is_dropped_but_the_candidate_with_valid_evidence_stays() -> None:
    entry = candidate("USD 5m")
    entry["evidence"].append({"doc_id": "D1", "quote": "this text is nowhere to be found"})
    script = Script(answer(("F1", [entry])))
    outcome = await extractor(script, max_retries=0).extract(GROUP, CONTEXT, BUNDLE)
    [kept] = outcome.candidates
    assert len(kept.evidence) == 1
    [warning] = outcome.warnings
    assert warning.code is WarningCode.CANDIDATE_DROPPED
    assert warning.document_id == DOC1


async def test_candidate_without_any_valid_evidence_is_dropped() -> None:
    bad = candidate("USD 5m", quote="this text is nowhere to be found")
    script = Script(answer(("F1", [bad, candidate("USD 5m")])))
    outcome = await extractor(script, max_retries=0).extract(GROUP, CONTEXT, BUNDLE)
    assert len(outcome.candidates) == 1
    assert len(outcome.warnings) == 1


async def test_nothing_valid_after_retries_fails_the_group() -> None:
    bad = answer(("F1", [candidate("USD 5m", quote="this text is nowhere to be found")]))
    script = Script(bad, bad, bad)
    with pytest.raises(GroupExtractionError) as raised:
        await extractor(script, max_retries=2).extract(GROUP, CONTEXT, BUNDLE)
    assert raised.value.group == "financials"
    assert "nowhere" in raised.value.reason


async def test_structurally_invalid_output_beyond_the_budget_fails_the_group() -> None:
    script = Script({"fields": "nope"}, {"fields": "nope"})
    with pytest.raises(GroupExtractionError):
        await extractor(script, max_retries=1).extract(GROUP, CONTEXT, BUNDLE)


# --- rate limiting through the extractor -----------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.time

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split())


def limited(script: Script, clock: FakeClock, max_wait: float = 600.0) -> LlmFieldExtractor:
    limiter = RateLimiter(RateLimitConfig(max_wait=max_wait), clock=clock)
    return LlmFieldExtractor(
        script.configured(), max_retries=2, rate_limiter=limiter, token_counter=WordCounter()
    )


async def test_429_is_waited_out_without_using_validation_attempts() -> None:
    clock = FakeClock()
    rate = ModelRateLimitError("m", "429", retry_after=5.0)
    bad = answer(("F9", [candidate("x")]))
    # two 429s, then an invalid answer, then a valid one: validation budget (2 retries) is untouched
    script = Script(rate, rate, bad, GOOD)
    outcome = await limited(script, clock).extract(GROUP, CONTEXT, BUNDLE)
    assert [c.field for c in outcome.candidates] == ["limit"]
    assert clock.sleeps == [5.0, 5.0]


async def test_server_error_is_retried_and_a_400_fails_the_group() -> None:
    clock = FakeClock()
    ok = Script(ModelHTTPError(503, "m"), GOOD)
    assert (await limited(ok, clock).extract(GROUP, CONTEXT, BUNDLE)).candidates
    bad_request = Script(ModelHTTPError(400, "m"))
    with pytest.raises(GroupExtractionError):
        await limited(bad_request, FakeClock()).extract(GROUP, CONTEXT, BUNDLE)


async def test_exhausted_waiting_budget_fails_the_group() -> None:
    script = Script(*[ModelRateLimitError("m", "429", retry_after=40.0) for _ in range(5)])
    with pytest.raises(GroupExtractionError):
        await limited(script, FakeClock(), max_wait=100.0).extract(GROUP, CONTEXT, BUNDLE)


# --- prompts ---------------------------------------------------------------------------------


def test_system_prompt_lists_fields_with_short_ids_and_formats() -> None:
    prompt = render_system(GROUP)
    for needle in ("F1", "limit", "money", "F2", "inception_date", "ISO 8601", "F3", "verbatim"):
        assert needle in prompt


def test_user_prompt_shows_documents_oldest_first_with_labels_and_dates() -> None:
    prompt = render_user(CONTEXT)
    assert prompt.index("D1") < prompt.index("D2")
    assert "2026-01-10" in prompt
    assert TEXT1 in prompt
    assert "schedule.pdf" in prompt
    assert str(DOC1) not in prompt  # real IDs never reach the model


def test_the_prompt_version_is_set() -> None:
    assert PROMPT_VERSION
