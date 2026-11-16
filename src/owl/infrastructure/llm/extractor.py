"""LLM field extraction with grounded, validated output (CONTRACT §7.2, §8).

One pydantic_ai agent run per field group. The model answers with a lenient structure; an output
validator checks IDs, values and quotes and raises one aggregated ``ModelRetry``. When the retry
budget is used up the invalid parts are dropped (``candidate_dropped``) and the rest is kept;
the group fails only if nothing valid remains (§8.4). Rate limits and transient errors are handled
below the agent, in ``RateLimitedModel``, outside the retry budget (§9.5).
"""

from dataclasses import dataclass, field
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models import Model

from owl.application.ports import ExtractionOutcome, GroupExtractionError, TokenCounter
from owl.domain.bundle import Document, DocumentBundle
from owl.domain.catalog import ActiveField, FieldGroup
from owl.domain.context import ExtractionContext
from owl.domain.evidence import (
    MatcherConfig,
    NormalisedText,
    QuoteMatcher,
    normalise,
    value_in_quotes,
)
from owl.domain.suggestions import (
    VALUE_NOT_IN_QUOTE,
    Candidate,
    Evidence,
    PipelineWarning,
    WarningCode,
)
from owl.domain.values import (
    DEFAULT_OPTIONS,
    FieldType,
    ParseOptions,
    Value,
    ValueParseError,
    ValueSpec,
    parse_value,
)
from owl.infrastructure.llm.model_factory import ConfiguredModel
from owl.infrastructure.llm.prompting import render_system, render_user
from owl.infrastructure.llm.rate_limit import RateLimitedModel, RateLimiter

# --- typed output model (CONTRACT §7.2) --------------------------------------------------------
#
# The output model is built per group from its active fields: every field gets its own candidate
# model whose ``value`` has the field's type, and ``field_id`` is a literal of the group's IDs, so
# a wrong shape or an unknown field is rejected by schema validation and pydantic_ai retries.
# The types stay lenient where the domain can canonicalise: the canonical form or plain text
# ("USD 5m", "01/03/2026", "15 %") is accepted, and the domain parser has the last word.


class MoneyIn(BaseModel):
    amount: str | int | float
    currency: str = Field(description="ISO 4217 code, e.g. USD")


class EvidenceIn(BaseModel):
    doc_id: str = Field(description="Document label, e.g. D2")
    quote: str = Field(description="Verbatim quote copied from that document")


class CandidateIn(BaseModel):
    """Base of the per-field candidate models; ``value`` is narrowed per field."""

    value: Any
    evidence: list[EvidenceIn] = Field(default_factory=lambda: list[EvidenceIn]())
    rationale: str = ""
    confidence: float = 0.5


class FieldOut(BaseModel):
    field_id: str
    candidates: list[CandidateIn]


class GroupOutput(BaseModel):
    fields: list[FieldOut] = Field(default_factory=lambda: list[FieldOut]())


_VALUE_HINTS: dict[FieldType, str] = {
    FieldType.STRING: "text, exactly as written",
    FieldType.INTEGER: "a whole number",
    FieldType.DECIMAL: "a number",
    FieldType.BOOL: "true or false",
    FieldType.DATE: "a date, ISO 8601 (YYYY-MM-DD)",
    FieldType.MONEY: 'an amount and ISO currency, e.g. {"amount": 5000000, "currency": "USD"}',
    FieldType.PERCENTAGE: 'a percentage, e.g. "15 %"',
    FieldType.ENUM: "one of the allowed values",
    FieldType.LIST: "a list of items",
}


def _value_type(spec: ValueSpec) -> Any:
    """The accepted JSON shape of a value: the canonical form, or plain text for the domain."""
    match spec.type:
        case FieldType.STRING | FieldType.DATE | FieldType.ENUM:
            return str
        case FieldType.INTEGER:
            return int | str
        case FieldType.DECIMAL | FieldType.PERCENTAGE:
            return int | float | str
        case FieldType.BOOL:
            return bool | str
        case FieldType.MONEY:
            return MoneyIn | str
        case FieldType.LIST:
            return _list_of(_value_type(spec.item_spec)) | str


def _value_description(active: ActiveField) -> str:
    definition = active.definition
    text = f"{definition.name}: {definition.description} Value: {_VALUE_HINTS[definition.type]}"
    if definition.allowed_values:
        text += f" (allowed: {', '.join(definition.allowed_values)})"
    return text


def _list_of(item: Any) -> Any:
    return list[item]  # pyright: ignore[reportInvalidTypeForm]


def _literal(tag: str) -> Any:
    return Literal[tag]  # pyright: ignore[reportInvalidTypeForm]


def build_output_model(group: FieldGroup) -> type[GroupOutput]:
    """The group's answer schema: one typed candidate model per active field."""
    entries: list[Any] = []
    for active in group.fields:
        description = _value_description(active)
        candidate: Any = create_model(
            f"Candidate{active.short_id}",
            __base__=CandidateIn,
            value=(_value_type(active.definition.value_spec), Field(description=description)),
        )
        entries.append(
            create_model(
                f"Field{active.short_id}",
                __base__=FieldOut,
                __doc__=description,
                field_id=(_literal(active.short_id), Field(description=description)),
                candidates=(_list_of(candidate), ...),
            )
        )
    item: Any = (
        entries[0]
        if len(entries) == 1
        else Annotated[reduce(or_, entries), Field(discriminator="field_id")]
    )
    return create_model(
        "GroupAnswer",
        __base__=GroupOutput,
        fields=(_list_of(item), Field(default_factory=list)),
    )


def _raw_value(value: object) -> object:
    if isinstance(value, MoneyIn):
        return {"amount": value.amount, "currency": value.currency}
    if isinstance(value, list):
        return [_raw_value(item) for item in value]
    return value


# --- validation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Problem:
    message: str
    field: str | None = None
    document: Document | None = None


@dataclass(frozen=True)
class _Checked:
    candidates: tuple[Candidate, ...]
    problems: tuple[_Problem, ...]


@dataclass
class _RunState:
    checked: _Checked = field(default_factory=lambda: _Checked((), ()))


class _GroupValidator:
    """Checks one model answer against the group, the shown documents and the full texts."""

    def __init__(
        self,
        group: FieldGroup,
        context: ExtractionContext,
        options: ParseOptions,
        matcher: QuoteMatcher,
    ) -> None:
        self._fields = {f.short_id: f for f in group.fields}
        self._shown = {d.labelled.short_id: d.labelled.document for d in context.documents}
        self._normalised: dict[str, NormalisedText] = {}
        self._options = options
        self._matcher = matcher

    def check(self, output: GroupOutput) -> _Checked:
        candidates: list[Candidate] = []
        problems: list[_Problem] = []
        for entry in output.fields:
            active = self._fields.get(entry.field_id)
            if active is None:
                known = ", ".join(self._fields)
                problems.append(
                    _Problem(f"field_id {entry.field_id!r} is unknown; valid field ids: {known}")
                )
                continue
            for item in entry.candidates:
                self._check_candidate(active, item, candidates, problems)
        return _Checked(tuple(candidates), tuple(problems))

    def _check_candidate(
        self,
        active: ActiveField,
        item: CandidateIn,
        candidates: list[Candidate],
        problems: list[_Problem],
    ) -> None:
        name = active.definition.name
        label = f"{active.short_id} ({name})"
        try:
            value = parse_value(active.definition.value_spec, _raw_value(item.value), self._options)
        except ValueParseError as error:
            problems.append(_Problem(f"{label}: value cannot be used: {error}", name))
            return
        if not item.evidence:
            problems.append(_Problem(f"{label}: value {item.value!r} has no evidence entry", name))
            return
        evidence: list[Evidence] = []
        for ref in item.evidence:
            found = self._evidence(label, ref, problems, name)
            if found is not None:
                evidence.append(found)
        if not evidence:
            return
        candidates.append(
            Candidate(
                field=name,
                value=value,
                evidence=tuple(evidence),
                rationale=item.rationale.strip(),
                confidence=min(1.0, max(0.0, item.confidence)),
                flags=self._flags(active, value, evidence),
            )
        )

    def _evidence(
        self, label: str, ref: EvidenceIn, problems: list[_Problem], name: str
    ) -> Evidence | None:
        document = self._shown.get(ref.doc_id)
        if document is None:
            shown = ", ".join(self._shown)
            problems.append(
                _Problem(
                    f"{label}: doc_id {ref.doc_id!r} is missing or not one of the documents "
                    f"shown ({shown})",
                    name,
                )
            )
            return None
        prepared = self._normalised.setdefault(ref.doc_id, normalise(document.text))
        match = self._matcher.match(ref.quote, prepared, original=document.text)
        if match is None:
            problems.append(
                _Problem(
                    f"{label}: quote {ref.quote!r} was not found in {ref.doc_id}; "
                    "copy it verbatim from the document",
                    name,
                    document,
                )
            )
            return None
        return Evidence(
            document.document_id,
            document.document_date,
            document.filename,
            match.source_text,
            match.kind,
        )

    def _flags(
        self, active: ActiveField, value: Value, evidence: list[Evidence]
    ) -> tuple[str, ...]:
        quotes = [e.quote for e in evidence]
        found = value_in_quotes(active.definition.value_spec, value, quotes, self._options)
        return () if found else (VALUE_NOT_IN_QUOTE,)


def _retry_message(problems: tuple[_Problem, ...]) -> str:
    lines = "\n".join(f"- {p.message}" for p in problems)
    return (
        f"Your answer has {len(problems)} problem(s). Fix all of them and answer again, "
        f"keeping everything that was correct:\n{lines}"
    )


# --- extractor ---------------------------------------------------------------------------------


class LlmFieldExtractor:
    """``FieldExtractor`` backed by a pydantic_ai agent (one run per group)."""

    def __init__(
        self,
        configured: ConfiguredModel,
        *,
        max_retries: int = 2,
        parse_options: ParseOptions = DEFAULT_OPTIONS,
        matcher_config: MatcherConfig | None = None,
        rate_limiter: RateLimiter | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        if (rate_limiter is None) != (token_counter is None):
            raise ValueError("rate_limiter and token_counter go together")
        self._configured = configured
        self._max_retries = max_retries
        self._options = parse_options
        self._matcher = QuoteMatcher(matcher_config or MatcherConfig())
        self._rate_limiter = rate_limiter
        self._counter = token_counter

    def _model(self) -> Model:
        model = self._configured.model
        if self._rate_limiter is None or self._counter is None:
            return model
        max_tokens = int(self._configured.model_settings.get("max_tokens", 0))
        return RateLimitedModel(
            model, self._rate_limiter, counter=self._counter, max_tokens=max_tokens
        )

    async def extract(
        self, group: FieldGroup, context: ExtractionContext, bundle: DocumentBundle
    ) -> ExtractionOutcome:
        del bundle  # quotes are checked against the full text carried by the context documents
        validator = _GroupValidator(group, context, self._options, self._matcher)
        agent: Agent[_RunState, GroupOutput] = Agent(
            self._model(),
            output_type=self._configured.output_spec(build_output_model(group)),
            deps_type=_RunState,
            instructions=render_system(group),
            model_settings=self._configured.model_settings,
            retries={"output": self._max_retries},
        )

        @agent.output_validator
        def _validate(ctx: RunContext[_RunState], output: GroupOutput) -> GroupOutput:
            checked = validator.check(output)
            ctx.deps.checked = checked
            exhausted = ctx.retry >= self._max_retries
            if checked.problems and not exhausted:
                raise ModelRetry(_retry_message(checked.problems))
            return output

        state = _RunState()
        try:
            await agent.run(render_user(context), deps=state)
        except (ModelAPIError, UnexpectedModelBehavior) as error:
            raise GroupExtractionError(group.name, f"{type(error).__name__}: {error}") from error
        return self._outcome(group, state.checked)

    @staticmethod
    def _outcome(group: FieldGroup, checked: _Checked) -> ExtractionOutcome:
        if checked.problems and not checked.candidates:
            reason = "; ".join(p.message for p in checked.problems)
            raise GroupExtractionError(group.name, f"nothing valid remained: {reason}")
        warnings = tuple(
            PipelineWarning(
                WarningCode.CANDIDATE_DROPPED,
                p.message,
                field=p.field,
                group=group.name,
                document_id=p.document.document_id if p.document else None,
            )
            for p in checked.problems
        )
        return ExtractionOutcome(checked.candidates, warnings)
