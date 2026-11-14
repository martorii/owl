# OWL — Implementation plan

Driven by [CONTRACT.md](CONTRACT.md). Each step is one feature branch.

## Workflow (applies to every step)

1. **Announce the step before starting**: tell the user exactly what will be done (files/modules, public
   interfaces, test list, design decisions and open questions, anything that looks underspecified in the
   contract), and **wait for the user's go-ahead**. Do not create the branch or write code before that.
2. Create the branch `feat/<NN>-<slug>` from the latest `main`.
3. Implement the step test-first. Keep to its scope; anything else goes to "Follow-ups" below.
4. **Gate**: all of the following must pass locally and in CI:
   - `uv run ruff check . && uv run ruff format --check .`
   - `uv run pyright`
   - `uv run lint-imports`
   - `uv run pytest` (with the step's listed tests present and green)
5. Report to the user: what was built, test results, anything that deviated from the contract.
   Every decision taken during the step that the contract does not already state (parsing rules, defaults,
   thresholds, names of public types) is listed in the report and, once approved, written into
   specs/CONTRACT.md in the same branch. Code must never be the only record of a decision.
6. **Only after the user accepts**: merge into `main` (PR, squash merge), delete the branch, tick the step below.
7. Never start step N+1 before step N is merged.

If a step requires changing specs/CONTRACT.md, stop and ask the user first.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` merged

---

## Step 00 — Bootstrap `[x]`
Branch: `feat/00-bootstrap`

- `pyproject.toml` (uv, Python 3.12), package `src/owl/` with empty `domain/`, `application/`,
  `infrastructure/`, `entrypoints/`.
- ruff, pyright (strict on domain + application), pytest + coverage, import-linter contracts (CONTRACT §12),
  pre-commit, `.gitignore`, `.env.example`.
- GitHub Actions `ci.yml`: sync → ruff → pyright → import-linter → pytest. Dependabot config.

**Gate tests**
- `tests/test_smoke.py`: package imports.
- import-linter passes with the layer contracts in place (and fails on a deliberately bad import, verified once by hand).

---

## Step 01 — Field value types `[x]`
Branch: `feat/01-value-types`

- `domain/values.py`: `FieldType` enum; per-type parser/normaliser and equality (CONTRACT §4), including
  `money`, `percentage`, `list` (set vs ordered), `enum`.
- Errors as domain exceptions (`ValueParseError`).

**Gate tests** (`tests/unit/domain/test_values.py`)
- Parsing tables per type (e.g. `"1,000,000 USD"`, `"USD 1m"` (abbreviations: accept or reject, decided in the step and documented), `"01/03/2026"`, `"15%"`, `"Yes"`), including rejects.
- Equality: case-insensitive strings, money currency mismatch ≠, list set vs ordered.
- Property-based tests (hypothesis): normalise is idempotent; equality is symmetric.
- Ambiguous inputs (e.g. day/month order) are documented in tests with the chosen rule.

---

## Step 02 — Catalog `[x]`
Branch: `feat/02-catalog`

- `domain/catalog.py`: `FieldDefinition`, `Applicability`, `Catalog` (version, active-field selection,
  grouping, short `F` IDs).
- `infrastructure/catalog_yaml.py`: YAML loader with validation.
- `catalog/fields.yaml`: first draft (~30 fields, all types used, all 4 LoB × business-type combinations
  covered). **User reviews the draft as part of acceptance.**

**Gate tests**
- Loader rejects: duplicate names, `list` without `item_type`, `enum` without `allowed_values`,
  unknown type/LoB/document type.
- Active fields correct for each of the 4 combinations; groups stable; `F` IDs follow catalog order.
- The shipped `catalog/fields.yaml` loads.

---

## Step 03 — Bundle and evidence `[x]`
Branch: `feat/03-bundle-evidence`

- `domain/bundle.py`: `Document`, `DocumentBundle`, `DocumentType`, UUID `document_id` (required, unique), prompt-only short `D` IDs.
- `domain/evidence.py`: text normaliser and `QuoteMatcher` (exact → fuzzy ≥ 25 chars → no match),
  returning the matched source span (CONTRACT §8.2).
- Value-in-quote check (§8.3).

**Gate tests**
- Matcher: whitespace/line breaks, curly quotes, dashes, hyphenation, case; fuzzy accepts OCR noise on long
  quotes; short quotes never fuzzy; replaced quote equals actual source text; no false match on unrelated text.
- Value-in-quote for date / money / percentage examples.

---

## Step 04 — Reconciliation `[x]`
Branch: `feat/04-reconciliation`

- `domain/suggestions.py`: `Candidate`, `Evidence`, `UpdateSuggestion`, `Conflict`, `PipelineWarning`
  (not `Warning`, which shadows the builtin), `SuggestionResult`;
  `reconcile(active_fields, candidates, current_values, mode)` (CONTRACT §7.3).
- `parse_current_values(catalog, active_fields, raw)`: unknown field → error, inactive field →
  `field_inactive_ignored` warning, unparseable value → error (CONTRACT §5.1).
- Value serialisation to the JSON forms of CONTRACT §6.1 (domain function; DTOs use it in step 10).

**To decide in the step announcement** (then record in CONTRACT §7.3):
- Merged candidates: which rationale is kept, which spelling is shown when values are equal but differ in
  case/format, whether identical evidence entries are de-duplicated.
- Whether a conflict candidate equal to the current value is marked as such.

**Gate tests**
- Table-driven: add / change / no-op / conflict / conflict including current value / format-variant
  duplicates merged / extraction mode → all `add`.
- A field is never in both suggestions and conflicts; confidence = max of merged.
- `parse_current_values`: unknown / inactive / unparseable cases.
- Serialisation round-trip (hypothesis): serialise → `parse_value` gives the same normalised value, for every type.

---

## Step 04.1 — Document dates and supersession `[~]`
Branch: `feat/04-1-document-dates`

Inserted after step 04 (user request): later documents must win when documents disagree.

- `Document.document_date` (required); `DocumentBundle` sorts chronologically, oldest first (stable).
- `Evidence.document_date`; `reconcile` supersession guardrail + `candidate_superseded` warning (CONTRACT §7.3).
- CONTRACT §5.1, §5.2, §6, §7.1, §7.2, §7.3 updated. Carried into later steps: selector priority newest-first
  within tier, presented chronologically (step 06); prompt shows each document's date and carries the
  supersession instruction (step 08); request DTO has `document_date` (step 10); golden-set documents have dates
  (step 05).

---

## Step 05 — Synthetic golden set `[ ]`
Branch: `feat/05-golden-set`

Moved ahead of the LLM steps: it depends only on the domain, and the extraction prompt (step 08) is then
developed against data.

- `evals/datasets/v1/cases.jsonl` (+ schema): ≈60 hand-authored cases per CONTRACT §14.1 covering all
  combinations, both modes, every difficulty tag (including `no_evidence` and `distractor`), every
  `DocumentType` of the taxonomy.
- Dataset validator.

**Gate tests**
- Every case validates; every planted value is literally present in its documents; expected outputs equal
  `reconcile` applied to the true record and current values (so `restated_old_value` cases expect the single
  correct suggestion, not a conflict); tag coverage report meets the targets above.

**User acceptance**: user reviews a sample of cases.

---

## Step 06 — Ports, settings, context selection `[ ]`
Branch: `feat/06-ports-context`

- `application/ports.py`: `FieldExtractor`, `ContextSelector`, `TokenCounter`, `Telemetry` (CONTRACT §11;
  with a no-op implementation), `MessageBus` (interface only).
- `infrastructure/settings.py`: pydantic-settings per CONTRACT §10 (roles + `default` fallback), including
  the parsing options of CONTRACT §4 (date order, decimal places, enum thresholds) and matcher thresholds.
- `infrastructure/context/type_filter.py`: `TypeFilterSelector`; `CharTokenCounter`.

**Gate tests**
- Settings: env parsing, role fallback to `default`, secrets not exposed in `repr`.
- Selector: preferred types first, then the remaining documents; nothing excluded while budget remains;
  budget respected; truncation → `document_truncated`; every document left out → `document_skipped`.

---

## Step 07 — Model factory `[ ]`
Branch: `feat/07-model-factory`

- `infrastructure/llm/model_factory.py`: `openai_compatible`, `azure_openai`, `openai`; output mode switch.

**Gate tests** (no network)
- Factory builds the right pydantic_ai model class and output mode for each provider (no calls made).
- No provider-specific import outside `ModelFactory`.

---

## Step 08 — LLM extractor `[ ]`
Branch: `feat/08-llm-extractor`

- `infrastructure/llm/extractor.py`: pydantic_ai agent per group; dynamic, lenient output model from active
  fields (canonical form or plain string, CONTRACT §7.2); every value passed through the domain parser;
  Jinja prompts with `prompt_version`; output validator implementing §8.1–8.4 (single aggregated
  `ModelRetry` message, drop after `max_retries`).

**To decide in the step announcement**: how "keep the valid part once retries are exhausted" (§8.4) is
implemented in pydantic_ai, which by default raises when retries run out (e.g. the validator accepts and
drops on the last attempt). Check the pinned pydantic_ai version's API first.

**Gate tests** (all with `FunctionModel`, no network)
- Happy path → typed candidates mapped back to real field names / document IDs.
- Non-canonical values from the model (`"USD 5m"`, `"01/03/2026"`, `"15 %"`) are canonicalised by the domain.
- Unknown `D`/`F` ID, missing ID, unmatched quote, unparseable value → retry message lists all problems →
  corrected answer accepted.
- Retries exhausted → partial keep + `candidate_dropped`; nothing valid → `group_failed`.

**Manual acceptance**: user runs one request against LM Studio with a provided script (a few golden-set
cases from step 05 are used as inputs).

---

## Step 09 — Pipeline and service `[ ]`
Branch: `feat/09-pipeline-service`

- `application/pipeline.py`: request validation → active fields → groups → parallel per-group
  (select context → extract) → join → reconcile. Spans via the `Telemetry` port.
- `application/service.py`: `suggest_updates`, `asuggest_updates`, `asuggest_updates_many` and extraction
  equivalents; request and global LLM concurrency limits; `InvalidRequestError`, `ExtractionFailedError`,
  `ExtractionTimeoutError`, `Failure` (CONTRACT §9.1).

**Gate tests**
- End-to-end with a fake `FieldExtractor`: both modes, a failing group does not fail the request
  (`group_failed` warning).
- All groups fail → `ExtractionFailedError`; timeout → `ExtractionTimeoutError`, no partial result.
- Warnings from selection, extraction and current-value parsing all reach the result.
- Concurrency: global LLM limit never exceeded (instrumented fake); batch returns `Failure` for a bad request
  without failing others; sync wrapper works outside and raises clearly inside a running loop.
- Unknown `current_values` field → error; inactive field → warning.
- Expected span names are emitted through a recording fake `Telemetry`.

---

## Step 10 — HTTP API `[ ]`
Branch: `feat/10-api`

- `entrypoints/api/`: FastAPI app, DTOs, `/v1/suggestions`, `/v1/extractions`, `/healthz`, `/readyz`,
  `X-Request-ID`, timeout, error mapping (422 / 502 / 504 per CONTRACT §9.1).
- `entrypoints/cli.py`: run a bundle JSON file through the service.

**Gate tests** (`TestClient`, fake extractor)
- Contract-shaped responses for both endpoints (JSON schema snapshot test of the response DTO), values
  serialised per CONTRACT §6.1.
- 422 cases: missing lob/business_type/document_type, empty documents, `current_values` on
  `/v1/extractions`, unparseable value.
- All groups fail → 502; timeout → 504.
- `/readyz` reflects model reachability (faked).

**Manual acceptance**: user calls the API against LM Studio.

---

## Step 11 — Observability `[ ]`
Branch: `feat/11-observability`

- `infrastructure/telemetry.py`: OTel adapter for the `Telemetry` port (traces, metrics, logs), no-op without
  endpoint, pydantic_ai + FastAPI instrumentation, structlog JSON with `trace_id`/`request_id`,
  capture-content flag.
- `docker-compose.yml`: app + OTel Collector + `grafana/otel-lgtm` (+ optional Phoenix profile).

**Gate tests**
- In-memory span exporter: expected span names and attributes per request; no prompt content when the flag is off.
- Metrics recorded for retries/drops/truncations/skipped documents.

---

## Step 12 — Container and CD `[ ]`
Branch: `feat/12-container-cd`

- Multi-stage `Dockerfile` (uv, `python:3.12-slim`, non-root, `HEALTHCHECK`).
- CI: Docker build + Trivy (report-only). `cd.yml`: push to GHCR on `main` (`sha-<short>`, `main`) and on
  tags `vX.Y.Z` (`X.Y.Z`, `latest`, GitHub Release).

**Gate tests**
- CI builds the image and runs a container smoke test (`/healthz` returns 200).

**Post-merge check** (not a merge gate): first push to GHCR verified.

---

## Step 13 — Eval harness `[ ]`
Branch: `feat/13-eval-harness`

- `evals/`: pydantic_evals dataset + evaluators for CONTRACT §14.2 metrics, `TextComparator` port with chrF
  (default), BLEU, token-F1; slicing with numerator/denominator per slice; JSON summary per run including
  the reproducibility record (§14.2); CLI `owl eval`.
- CI `eval.yml`: `workflow_dispatch` only (disabled by default).

**Gate tests**
- Metric unit tests on hand-computed examples (recall / precision / false-change rate per the §14.2
  formulas / conflict metrics, including a `restated_old_value` false conflict).
- Harness run on a handful of cases with a scripted `FunctionModel` produces the expected summary.

**Manual acceptance**: user runs the full eval against LM Studio; the result is the first baseline.

---

## Later (not scheduled — require decisions in CONTRACT §16 first)
- Bus (`MessageBus` adapters, consumer entrypoint).
- Cross-field validation; formatting and polishing.
- `HybridRetrievalSelector` + `Embedder`.
- Eval gating; LLM-as-judge comparator.

## Follow-ups
(Items found during implementation that are out of the current step's scope.)

- Still undefined in the contract, deliberately left for later: error response body shape, authentication,
  request size limits, `null` in `current_values`, duplicate / foreign `field_id` in LLM output, scope of the
  "global" LLM limit with several workers, a no-logging rule for document text, output ordering.
