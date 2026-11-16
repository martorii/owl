"""Manual acceptance of the LLM extractor against a real model (PLAN step 08).

Run by the user, never in CI. Configure the model through ``OWL_MODELS__EXTRACTION__*`` (or
``OWL_MODELS__DEFAULT__*``), e.g. for LM Studio:

    OWL_MODELS__DEFAULT__PROVIDER=openai_compatible OWL_MODELS__DEFAULT__NAME=<model> \\
    OWL_MODELS__DEFAULT__BASE_URL=http://localhost:1234/v1 \\
    OWL_MODELS__DEFAULT__CONTEXT_WINDOW=32768 \\
    uv run python -m evals.try_extractor            # first few cases
    uv run python -m evals.try_extractor case-id…   # chosen cases

Prints, per field group, the candidates and warnings, and marks each planted ``true`` value as
found or missed (compared after domain normalisation).
"""

import asyncio
import sys
from collections.abc import Sequence

from evals.schema import GoldenCase, PlantedRole
from evals.validate_dataset import CATALOG_PATH, load_cases
from owl.application.ports import GroupExtractionError
from owl.domain.bundle import Document, DocumentBundle
from owl.domain.catalog import group_fields
from owl.domain.values import format_value, parse_value, values_equal
from owl.infrastructure.catalog_yaml import load_catalog
from owl.infrastructure.context.token_counter import CharTokenCounter
from owl.infrastructure.context.type_filter import TypeFilterSelector
from owl.infrastructure.llm.extractor import LlmFieldExtractor
from owl.infrastructure.llm.model_factory import ModelFactory
from owl.infrastructure.llm.rate_limit import RateLimitConfig, RateLimiter
from owl.infrastructure.settings import ModelRole, Settings

DEFAULT_CASES = 3


async def run_case(case: GoldenCase, extractor: LlmFieldExtractor, settings: Settings) -> None:
    catalog = load_catalog(CATALOG_PATH)
    bundle = DocumentBundle(
        tuple(
            Document(d.document_id, d.document_date, d.text, d.filename, d.document_type)
            for d in case.documents
        )
    )
    selector = TypeFilterSelector(CharTokenCounter())
    budget = settings.context_budget()
    tags = [t.value for t in case.tags]
    print(f"\n=== {case.case_id} ({case.lob.value}, {case.mode.value}) tags={tags}")
    for group in group_fields(catalog.active_fields(case.lob, case.business_type)):
        context = selector.select(group, bundle, budget)
        try:
            outcome = await extractor.extract(group, context, bundle)
        except GroupExtractionError as error:
            print(f"-- {group.name}: GROUP FAILED: {error.reason}")
            continue
        print(f"-- {group.name}: {len(outcome.candidates)} candidate(s)")
        specs = {f.definition.name: f.definition.value_spec for f in group.fields}
        for c in outcome.candidates:
            print(f"   {c.field} = {format_value(specs[c.field], c.value)}  flags={list(c.flags)}")
        for w in outcome.warnings:
            print(f"   ! {w.code.value}: {w.message}")
        for p in case.planted:
            if p.role is not PlantedRole.TRUE or p.field not in specs:
                continue
            want = parse_value(specs[p.field], p.value)
            hit = any(
                c.field == p.field and values_equal(specs[p.field], c.value, want)
                for c in outcome.candidates
            )
            print(f"   planted {p.field} = {p.value!r}: {'FOUND' if hit else 'MISSED'}")


async def main(case_ids: Sequence[str]) -> None:
    settings = Settings()
    configured = ModelFactory(settings.models).build(ModelRole.EXTRACTION)
    model = settings.models.model_for(ModelRole.EXTRACTION)
    limiter = RateLimiter(
        RateLimitConfig.for_process(
            tpm_limit=model.tpm_limit,
            max_concurrent_runs=settings.jobs.max_concurrent_runs,
            max_wait=model.rate_limit_max_wait,
        )
    )
    extractor = LlmFieldExtractor(
        configured,
        max_retries=model.max_retries,
        parse_options=settings.parsing.to_parse_options(),
        matcher_config=settings.matcher.to_matcher_config(),
        rate_limiter=limiter,
        token_counter=CharTokenCounter(),
    )
    cases = load_cases()
    chosen = [c for c in cases if c.case_id in case_ids] if case_ids else cases[:DEFAULT_CASES]
    for case in chosen:
        await run_case(case, extractor, settings)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
