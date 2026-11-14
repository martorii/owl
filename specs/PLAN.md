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

## Step 01 — Field value types `[~]`
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

## Step 02 — Catalog `[ ]`
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

## Step 03 — Bundle and evidence `[ ]`
Branch: `feat/03-bundle-evidence`

- `domain/bundle.py`: `Document`, `DocumentBundle`, `DocumentType`, short `D` IDs, default `doc-<index>` IDs.
- `domain/evidence.py`: text normaliser and `QuoteMatcher` (exact → fuzzy ≥ 25 chars → no match),
  returning the matched source span (CONTRACT §8.2).
- Value-in-quote check (§8.3).

**Gate tests**
- Matcher: whitespace/line breaks, curly quotes, dashes, hyphenation, case; fuzzy accepts OCR noise on long
  quotes; short quotes never fuzzy; replaced quote equals actual source text; no false match on unrelated text.
- Value-in-quote for date / money / percentage examples.

---

## Step 04 — Reconciliation `[ ]`
Branch: `feat/04-reconciliation`

- `domain/suggestions.py`: `Candidate`, `Evidence`, `UpdateSuggestion`, `Conflict`, `Warning`,
  `SuggestionResult`; `reconcile(active_fields, candidates, current_values, mode)` (CONTRACT §7.3).

**Gate tests**
- Table-driven: add / change / no-op / conflict / conflict including current value / format-variant
  duplicates merged / extraction mode → all `add`.
- A field is never in both suggestions and conflicts; confidence = max of merged.

---

## Step 05 — Ports, settings, context selection `[ ]`
Branch: `feat/05-ports-context`

- `application/ports.py`: `FieldExtractor`, `ContextSelector`, `TokenCounter`, `MessageBus` (interface only).
- `infrastructure/settings.py`: pydantic-settings per CONTRACT §10 (roles + `default` fallback).
- `infrastructure/context/type_filter.py`: `TypeFilterSelector`; `CharTokenCounter`.

**Gate tests**
- Settings: env parsing, role fallback to `default`, secrets not exposed in `repr`.
- Selector: preferred types first, fallback to all docs, budget respected, truncation + warning.

---

## Step 06 — LLM extractor `[ ]`
Branch: `feat/06-llm-extractor`

- `infrastructure/llm/model_factory.py`: `openai_compatible`, `azure_openai`, `openai`; output mode switch.
- `infrastructure/llm/extractor.py`: pydantic_ai agent per group; dynamic output model from active fields;
  Jinja prompts with `prompt_version`; output validator implementing §8.1–8.4 (single aggregated
  `ModelRetry` message, drop after `max_retries`).

**Gate tests** (all with `FunctionModel`, no network)
- Happy path → typed candidates mapped back to real field names / document IDs.
- Unknown `D`/`F` ID, missing ID, unmatched quote → retry message lists all problems → corrected answer accepted.
- Retries exhausted → partial keep + `candidate_dropped`; nothing valid → `group_failed`.
- Factory builds the right pydantic_ai model class for each provider (no calls made).

**Manual acceptance**: user runs one request against LM Studio with a provided script.

---

## Step 07 — Pipeline and service `[ ]`
Branch: `feat/07-pipeline-service`

- `application/pipeline.py`: request validation → active fields → groups → parallel per-group
  (select context → extract) → join → reconcile.
- `application/service.py`: `suggest_updates`, `asuggest_updates`, `asuggest_updates_many` and extraction
  equivalents; request and global LLM concurrency limits.

**Gate tests**
- End-to-end with a fake `FieldExtractor`: both modes, a failing group does not fail the request.
- Concurrency: global LLM limit never exceeded (instrumented fake); batch returns `Failure` for a bad request
  without failing others; sync wrapper works outside and raises clearly inside a running loop.
- Unknown `current_values` field → error; inactive field → warning.

---

## Step 08 — HTTP API `[ ]`
Branch: `feat/08-api`

- `entrypoints/api/`: FastAPI app, DTOs, `/v1/suggestions`, `/v1/extractions`, `/healthz`, `/readyz`,
  `X-Request-ID`, timeout, error mapping (422 for invalid input).
- `entrypoints/cli.py`: run a bundle JSON file through the service.

**Gate tests** (`TestClient`, fake extractor)
- Contract-shaped responses for both endpoints (JSON schema snapshot test of the response DTO).
- 422 cases: missing lob/business_type, empty documents, `current_values` on `/v1/extractions`, unparseable value.
- `/readyz` reflects model reachability (faked).

**Manual acceptance**: user calls the API against LM Studio.

---

## Step 09 — Observability `[ ]`
Branch: `feat/09-observability`

- `infrastructure/telemetry.py`: OTel setup (traces, metrics, logs), no-op without endpoint, pipeline spans,
  pydantic_ai + FastAPI instrumentation, structlog JSON with `trace_id`/`request_id`, capture-content flag.
- `docker-compose.yml`: app + OTel Collector + `grafana/otel-lgtm` (+ optional Phoenix profile).

**Gate tests**
- In-memory span exporter: expected span names and attributes per request; no prompt content when the flag is off.
- Metrics recorded for retries/drops/truncations.

---

## Step 10 — Container and CD `[ ]`
Branch: `feat/10-container-cd`

- Multi-stage `Dockerfile` (uv, `python:3.12-slim`, non-root, `HEALTHCHECK`).
- CI: Docker build + Trivy (report-only). `cd.yml`: push to GHCR on `main` (`sha-<short>`, `main`) and on
  tags `vX.Y.Z` (`X.Y.Z`, `latest`, GitHub Release).

**Gate tests**
- CI builds the image and runs a container smoke test (`/healthz` returns 200).
- First push to GHCR verified after merge.

---

## Step 11 — Synthetic golden set `[ ]`
Branch: `feat/11-golden-set`

- `evals/datasets/v1/cases.jsonl` (+ schema): ≈60 hand-authored cases per CONTRACT §14.1 covering all
  combinations, both modes, every difficulty tag, every document type.
- Dataset validator.

**Gate tests**
- Every case validates; every planted value is literally present in its documents; expected outputs are
  consistent with `reconcile` applied to the true record and current values; tag coverage report meets the
  targets above.

**User acceptance**: user reviews a sample of cases.

---

## Step 12 — Eval harness `[ ]`
Branch: `feat/12-eval-harness`

- `evals/`: pydantic_evals dataset + evaluators for CONTRACT §14.2 metrics, `TextComparator` port with chrF
  (default), BLEU, token-F1; slicing; JSON summary per run; CLI `owl eval`.
- CI `eval.yml`: `workflow_dispatch` only (disabled by default).

**Gate tests**
- Metric unit tests on hand-computed examples (recall/precision/false-change/conflict metrics).
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
