# OWL — Contract

This document records the decisions that drive development. Code must conform to it.
Any change to a decision here requires explicit user approval and is made in this file first,
in the same branch as the code that depends on it (see "Change process" at the end).

Decisions marked **(default)** were not explicitly discussed; they are sensible defaults that may be revisited.
Items under **Parked** are deliberately undecided — do not implement them until they are decided here.

---

## 1. Purpose

Given a **document bundle** (already OCR'd text with metadata) and optionally the **current field values**
of an insurance record, return a list of **update suggestions** (and **conflicts**) for those fields.
Every suggestion must be grounded in verbatim quotes from the bundle. All output is reviewed by a human,
so the system favours **recall over precision**: it is better to suggest than to omit.

## 2. Glossary (ubiquitous language)

| Term | Meaning |
|---|---|
| **Bundle** | The set of documents submitted in one request. |
| **Document** | One OCR'd text with `filename` and `document_type`. |
| **Document type** | A value of `DocumentType` in `owl.domain.taxonomy`: `contract`, `slip`, `endorsement`, `email`, `claims_report`, `other`. |
| **LoB** | Line of business: `casualty` or `property`. |
| **Business type** | `direct` or `reinsurance`. |
| **Catalog** | The versioned YAML list of field definitions. |
| **Active fields** | Catalog fields applicable to the request's `(lob, business_type)`. |
| **Field group** | A set of fields extracted together in one LLM call. |
| **Candidate** | A value found by the LLM for a field, with evidence. |
| **Evidence** | A `(document, quote)` pair supporting a candidate. |
| **Suggestion** | A proposed `add` or `change` to a field. |
| **Conflict** | A field for which two or more distinct values were found. |
| **Mode** | `update` (current values provided) or `extraction` (no current values). |

## 3. Field catalog

- Lives in `catalog/fields.yaml`, loaded at startup into typed domain `FieldDefinition` objects. A malformed
  catalog fails startup.
- The catalog carries a `version`: a positive integer, bumped on every change. Every response, trace and eval run
  records it.
- Closed vocabularies (`Lob`, `BusinessType`, `DocumentType`) are defined once, in `owl.domain.taxonomy`; the
  catalog and requests may only use those values. Document types: `contract`, `slip`, `endorsement`, `email`,
  `claims_report`, `other`.
- Field definition:

```yaml
version: 1
fields:
  - name: policy_inception_date          # unique, snake_case
    type: date                           # see §4
    item_type: null                      # required iff type == list
    ordered: false                       # only meaningful for list
    allowed_values: null                 # required iff type == enum (or item_type == enum)
    description: Start date of the policy period.   # also used in the extraction prompt
    applies_to:
      lob: [casualty, property]
      business_type: [direct, reinsurance]
    group: policy_terms
    preferred_document_types: [contract, email]
```

- Active fields = fields whose `applies_to` contains both the request's `lob` and `business_type`.
- The first catalog is drafted by Claude during implementation (≈30 fields, groups such as `parties`,
  `policy_terms`, `financials`, `property_risk`, `casualty_risk`, `claims`) and will later be replaced by the
  real one without code changes.

## 4. Field types

Closed set. Each type has a **parser/normaliser** and an **equality** rule in the domain. "Change" and
"conflict" decisions are always made on normalised values.

| Type | Normalised form | Equal when |
|---|---|---|
| `string` | trimmed, whitespace collapsed | equal case-insensitively |
| `integer` | `int` (thousands separators removed) | exact |
| `decimal` | `Decimal`, rounded half-up to the configured precision (default 6 places) | exact after rounding |
| `bool` | `true`/`false` (from yes/no, Y/N, true/false, …) | exact |
| `date` | ISO `YYYY-MM-DD` | exact |
| `money` | `{amount: Decimal, currency: ISO-4217}` | amount and currency equal |
| `percentage` | `Decimal` fraction (`15%` → `0.15`) | exact |
| `enum` | one of `allowed_values` (matched leniently, see below) | exact |
| `list` | list of `item_type` values | as a set; as a sequence if `ordered: true` |

Parsing rules (the domain parsers are lenient on input, strict on the normalised form):

- **Numbers** are locale-tolerant: `1,000.50`, `1.000,50`, `1 000,50` and `1'000.50` are accepted. A lone
  separator followed by exactly three digits (`1.000`) is thousands grouping for `integer` and `money`, and a
  decimal point for `decimal` and `percentage`.
- **Dates**: ISO; dotted `D.M.YYYY` is always day-first; in `D/M/YYYY` and `D-M-YYYY` a part > 12 is the day,
  and if both parts are ≤ 12 the order is configurable (default day-first). Spelled-out months are accepted in
  en, de, fr, es, it, nl, pt. Four-digit years only; two-digit years are never guessed.
- **Money**: a currency is mandatory (ISO code, symbol or name → ISO code). Magnitude abbreviations are
  accepted (`500k`, `1m`, `2 Mrd`, …). `billion` means 10^9; German `Billion` (10^12) is not supported.
- **Percentage**: text needs an explicit `%` or the word "percent" (and equivalents); bare numbers are
  rejected as ambiguous.
- **Enum**: matched case-insensitively against `allowed_values`, then by Jaro-Winkler similarity
  (threshold 0.92, margin 0.03 to the runner-up, both configurable); anything else is rejected.

No free-form `object` type. A `record` type with sub-fields may be added later.

## 5. Inputs

### 5.1 Request (both modes)

```json
{
  "lob": "property",
  "business_type": "reinsurance",
  "documents": [
    {"document_id": "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b", "filename": "slip.pdf", "document_type": "contract", "text": "..."}
  ],
  "current_values": {"policy_inception_date": "2026-01-01"}
}
```

- `lob` and `business_type` are **required**. No inference (a `ClassifyBundle` step may be added later).
- `documents` must be non-empty; each `text` must be non-blank. `document_type` is **required** (use `other`
  when unknown).
- `document_id` is **required**: the document's UUID, supplied by the caller. It is the only identifier of a
  document and is what evidence returns. Duplicate or malformed UUIDs → `422`.
- `current_values` is required and may be empty in `update` mode; it is not accepted in `extraction` mode.
- `current_values` keys must be catalog field names; unknown names → `422` **(default)**. Fields not active
  for the request are ignored with a warning **(default)**. Values are parsed with the field's type
  parser; unparseable values → `422` **(default)**. A `null` value means "no current value": it is treated as
  absent, without a warning.

### 5.2 Internal short IDs

- In prompts, documents are labelled `D1, D2, …` and active fields `F1, F2, …` (catalog order).
- The mapping is per request and lives in the domain. Short IDs are prompt labels, not identifiers (a document is
  identified only by its UUID), and never appear in responses.

## 6. Output

```json
{
  "request_id": "…",
  "catalog_version": 1,
  "prompt_version": "…",
  "mode": "update",
  "suggestions": [
    {
      "field": "policy_inception_date",
      "action": "change",
      "current_value": "2026-01-01",
      "proposed_value": "2026-03-01",
      "evidence": [
        {"document_id": "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b", "filename": "slip.pdf", "quote": "Period: 01/03/2026 to 28/02/2027", "match": "exact"}
      ],
      "rationales": ["The slip states a 1 March inception."],
      "confidence": 0.86,
      "flags": []
    }
  ],
  "conflicts": [
    {
      "field": "limit",
      "current_value": {"amount": "5000000", "currency": "USD"},
      "candidates": [
        {"value": {"amount": "5000000", "currency": "USD"}, "is_current": true, "evidence": [], "rationales": ["…"], "confidence": 0.7, "flags": []},
        {"value": {"amount": "7500000", "currency": "USD"}, "is_current": false, "evidence": [], "rationales": ["…"], "confidence": 0.8, "flags": []}
      ]
    }
  ],
  "warnings": [
    {"code": "candidate_dropped", "message": "…", "field": "limit", "group": "financials", "document_id": null}
  ]
}
```

Rules:
- `action` ∈ {`add`, `change`}. There is **no `confirm`** and **no `remove`**.
- Only changes are output: a field whose single found value equals the current value produces nothing.
- A field appears in **either** `suggestions` **or** `conflicts`, never both.
- A conflict is reported whenever ≥ 2 distinct normalised values are found, **even if one equals the current value**.
- `evidence` has at least one entry; candidates with no valid evidence are dropped.
- `match` ∈ {`exact`, `fuzzy`}. A fuzzy-matched quote is replaced by the actual source text.
- `confidence` is the model's self-report (max over merged candidates). It is informational only: **not used
  for filtering, sorting or any other decision**.
- `rationales` lists the rationale of every merged candidate (§7.3); a conflict candidate's `is_current` is
  true when its value equals the field's current value.
- Output order: suggestions and conflicts follow the catalog order of the active fields.
- `flags` currently only contains `value_not_in_quote` (§8.3).
- In `extraction` mode the shape is identical; all actions are `add` and `current_value` is `null`.
- Warning codes (initial set): `candidate_dropped`, `document_truncated`, `document_skipped`, `group_failed`,
  `field_inactive_ignored`.

### 6.1 Value serialisation

Values (`current_value`, `proposed_value`, `value`) are serialised from the normalised form (§4):

| Type | JSON |
|---|---|
| `string`, `enum` | string |
| `integer` | number |
| `decimal` | string (`"1234.5"`), never a float |
| `bool` | boolean |
| `date` | string, ISO `YYYY-MM-DD` |
| `money` | `{"amount": "5000000", "currency": "USD"}` (amount as string) |
| `percentage` | string with percent sign (`"15%"`; the normalised fraction `0.15` is internal) |
| `list` | array of the item type's JSON form |

Every serialised value parses back to the same normalised value, so a response value can be sent back
unchanged in `current_values`.

## 7. Pipeline

```
validate request → select active fields → group fields
   → per group, in parallel:  select context → extract (LLM) → validate output (ModelRetry loop)
   → join → normalise & reconcile (domain) → [cross-field validation: parked] → [polishing: parked] → response
```

### 7.1 Context selection

- Port: `ContextSelector.select(group, bundle, budget) -> ExtractionContext`.
- First implementation, `TypeFilterSelector`:
  1. Order all documents: first those whose type is in any of the group's `preferred_document_types`, then
     the rest; input order within each tier. No document is excluded by type alone (recall over precision).
  2. Add whole documents in that order until the token budget is reached.
  3. A document larger than the remaining budget is cut at the end (keeps the start), with a
     `document_truncated` warning.
  4. Every document left out entirely once the budget is exhausted produces a `document_skipped` warning
     (with its `document_id` and the group).
- Budget = configured fraction of the extraction model's `CONTEXT_WINDOW` (default 0.6). The budget covers
  document text only; the remaining fraction is the reserve for the prompt (instructions, field
  descriptions) and the output (`MAX_TOKENS`).
- Future `HybridRetrievalSelector` (chunking + BM25 + embeddings + RRF, `Embedder` port) plugs into the
  same port and is chosen by config.
- Quotes are always validated against the **full** document text, not the selected excerpt.

### 7.2 Extraction output (per group, structured output)

```json
{
  "fields": [
    {
      "field_id": "F3",
      "candidates": [
        {
          "value": {"amount": 5000000, "currency": "USD"},
          "evidence": [{"doc_id": "D2", "quote": "Limit: USD 5,000,000 any one occurrence"}],
          "rationale": "…",
          "confidence": 0.8
        }
      ]
    }
  ]
}
```

- Fields not found are **omitted** (never `null`). A candidate `value` of `null` is not allowed.
- `value` is typed per field (the per-group output model is built dynamically from the active fields).
- **Normalisation is two-layered.** The LLM is the first layer: it is asked to return each value already in
  the canonical form. The domain is the second and authoritative layer: every returned value goes through
  the field's type parser (§4), which canonicalises whatever the LLM did not (e.g. `"USD 5m"`,
  `"01/03/2026"`, `"15 %"`). The output model is therefore lenient: it accepts the canonical form or a plain
  string for any type. All "change" and "conflict" decisions use the domain-normalised value only.
- A value the domain parser cannot parse is a validation failure (§8.1).
- The model is instructed to report **every** distinct value it finds, not to resolve disagreements.

### 7.3 Reconciliation (domain, deterministic)

Per field:
1. Normalise candidate values; merge candidates with equal normalised values (union evidence, max confidence).
2. One distinct value ≠ current → suggestion (`add` if current is absent, else `change`).
3. One distinct value = current → nothing.
4. ≥ 2 distinct values → conflict.

Merging candidates with equal normalised values:
- Value shown: the spelling of the highest-confidence candidate (first one on ties); `confidence` = max.
- Rationales: all kept, trimmed, blank ones dropped, exact duplicates removed, in input order.
- Evidence: union in input order; entries with the same `(document_id, quote)` are one entry (the first is
  kept, including its `match`). Deliberately simple; smarter de-duplication (overlapping quotes) may follow.
- Flags: union.
- In a conflict, each distinct value is one merged candidate in first-seen order, marked `is_current` when it
  equals the current value.

Known accepted limitation: restated old values ("previously USD 5m, now USD 7.5m") produce false conflicts;
they are measured in evals and left to the reviewer.

## 8. Output validation

Implemented as pydantic_ai output validators raising `ModelRetry`.

### 8.1 ID and value checks
- Missing or unknown `doc_id` or `field_id` → `ModelRetry`.
- A `value` the field's type parser rejects (§7.2) → `ModelRetry`.

### 8.2 Quote matcher (per evidence entry)
1. **Normalised exact match**: NFKC, unify quotes/dashes, collapse whitespace, ignore case,
   remove line-break hyphenation. Match → `match: exact`.
2. **Fuzzy fallback**, only if the normalised quote has ≥ 25 characters (configurable): best window with
   rapidfuzz `partial_ratio` ≥ 92 (configurable). Match → quote replaced with the source text, `match: fuzzy`.
3. **No match** → `ModelRetry`.

### 8.3 Value-in-quote check (soft)
The field's type parser must find the proposed value in at least one quote. For a `list` value, every item
must be found in at least one quote (not necessarily the same one). If not, the candidate is kept with flag
`value_not_in_quote`.

### 8.4 Retry policy
- One `ModelRetry` message lists **all** failures of that output.
- `max_retries` default 2 (configurable).
- When retries are exhausted, invalid evidence/candidates are dropped with `candidate_dropped` warnings
  and the rest of the group output is kept. The group fails (`group_failed`) only if nothing valid remains.
  A failed group never fails the whole request.

## 9. Interfaces

### 9.1 HTTP (FastAPI)
- `POST /v1/suggestions` — update mode.
- `POST /v1/extractions` — extraction mode (no `current_values`).
- `GET /healthz` — liveness. `GET /readyz` — pings the configured extraction model endpoint.
- Synchronous request/response with configurable timeout. `request_id` is generated (or taken from an
  `X-Request-ID` header) and echoed. No job API until the bus exists.
- API request/response models are separate DTOs mapped to/from domain objects.
- Errors:

  | Situation | HTTP | Library |
  |---|---|---|
  | Invalid request (§5.1) | `422` | `InvalidRequestError` |
  | **Every** group failed (e.g. the model is unreachable) | `502` | `ExtractionFailedError` |
  | Request timeout exceeded | `504` | `ExtractionTimeoutError` |

  A response with empty `suggestions` and `conflicts` therefore always means "nothing to change", never
  "extraction did not run". If at least one group succeeds the request succeeds (`200`) and the failed groups
  are reported as `group_failed` warnings. On timeout no partial result is returned. In the `*_many`
  functions these errors are returned as `Failure` items instead of being raised.

### 9.2 Python library
```python
suggest_updates(request) -> SuggestionResult                 # sync wrapper
await asuggest_updates(request) -> SuggestionResult          # async core
await asuggest_updates_many(requests, max_concurrency=4) -> list[SuggestionResult | Failure]
```
(and the equivalent `extract_fields` / `aextract_fields` / `aextract_fields_many` for extraction mode)

### 9.3 Concurrency
- asyncio only (no threads).
- Two configurable limits: concurrent requests, and concurrent LLM calls **globally** (across requests).

### 9.4 Bus — **Parked**
A `MessageBus` port and an in-process adapter will exist so that an external broker can replace it.
Request/reply vs fire-and-forget, result delivery, retries, DLQ, idempotency and target broker are undecided.

## 10. LLM and models

- Settings via pydantic-settings (env / `.env`), prefix `OWL_`, nested delimiter `__`.
- Models configured **per role**: `extraction` (now), `polishing`, `judge` (later). Unconfigured roles fall
  back to `default`.
- Per role: `PROVIDER` ∈ {`openai_compatible`, `azure_openai`, `openai`}, `NAME`/`DEPLOYMENT`, `BASE_URL` /
  `AZURE_ENDPOINT` / `API_VERSION`, `API_KEY` (SecretStr), `CONTEXT_WINDOW`, `TEMPERATURE` (default 0),
  `MAX_TOKENS`, `TIMEOUT`, `MAX_RETRIES`, `OUTPUT_MODE` ∈ {`native` (default), `tool`, `prompted`}.
- A single `ModelFactory` (infrastructure) maps settings → pydantic_ai `Model`. No other module knows the provider.
- Current provider: LM Studio via `openai_compatible`. Target swap: Azure OpenAI.
- Token counting: `TokenCounter` port. Default: characters ÷ 3.5 with a 15% safety margin. `tiktoken`
  counter available for OpenAI/Azure.
- Prompts are versioned Jinja templates in `src/owl/infrastructure/llm/prompts/`; `prompt_version` is recorded.

## 11. Observability

- OpenTelemetry for traces, metrics and logs. Export via OTLP to an **OTel Collector**; the app never talks
  to a backend directly. No endpoint configured → no-op.
- Spans: `pipeline.select_fields`, `pipeline.select_context`, `pipeline.extract`, `pipeline.validate`,
  `pipeline.reconcile` (per group where relevant). LLM spans via pydantic_ai instrumentation (GenAI
  semantic conventions). FastAPI auto-instrumented.
- Prompt/completion capture is off by default (`OWL_OTEL__CAPTURE_CONTENT=false`).
- Metrics: requests/latency per endpoint; LLM calls/tokens/retries per role; dropped candidates by reason; truncations.
- Logs: structured JSON (`structlog`) carrying `trace_id` and `request_id`.
- Local stack (docker-compose): app + Collector + `grafana/otel-lgtm`, optional Arize Phoenix.
  Production backend: undecided (likely Azure Monitor).
- The domain and application layers have no OTel dependency. The application emits spans and metrics through
  a `Telemetry` port (`span(name, attributes)` context manager, counters/histograms); the default adapter is a
  no-op and the OTel adapter lives in infrastructure. Application code logs through stdlib `logging`;
  `structlog` is configured in infrastructure as the renderer.

## 12. Architecture

DDD, layered, enforced with import-linter:

```
src/owl/
  domain/          pure Python + pydantic + rapidfuzz (pure computation); no I/O, no pydantic_ai, no OTel,
                   no FastAPI
  application/     ports (Protocols), use cases, pipeline orchestration, service functions
  infrastructure/  pydantic_ai agent, ModelFactory, prompts, context selectors, bus adapters,
                   YAML catalog loader, telemetry, settings
  entrypoints/     FastAPI app, CLI
catalog/fields.yaml
evals/             datasets/, comparators, runner, reports
tests/             unit/, pipeline/, api/, contract/
```

Import rules:
- `domain` imports nothing from `application`, `infrastructure`, `entrypoints`.
- `application` imports only `domain`.
- Only `infrastructure` imports `pydantic_ai`, `opentelemetry` exporters, `httpx`, `yaml`.
- `entrypoints` wires everything together (composition root).

## 13. Quality, CI and CD

- Tooling: Python 3.12, uv, ruff (lint + format), pyright (strict on `domain/` and `application/`),
  pytest, import-linter, pre-commit, Dependabot (uv + actions), Conventional Commits.
- CI (GitHub Actions, every PR and push):
  `uv sync --frozen` → ruff → pyright → pytest (coverage ≥ 85% on domain + application) → import-linter →
  Docker build + Trivy scan (report-only) → eval job (**present but disabled**: `workflow_dispatch` only).
- Tests never call a real LLM. Pipeline tests use pydantic_ai `FunctionModel`/`TestModel`.
- CD: merge to `main` → push image to GHCR as `ghcr.io/<owner>/owl:sha-<short>` and `:main`;
  tag `vX.Y.Z` → `:X.Y.Z` + `:latest` + GitHub Release. No deployment target yet.
- Image: multi-stage, uv, `python:3.12-slim`, non-root, `HEALTHCHECK` on `/healthz`, configured by env only.

## 14. Evaluation

### 14.1 Golden set
- Fully **synthetic** (no real data is used for now). Hand-authored by Claude during implementation,
  committed frozen as versioned JSONL under `evals/datasets/`. A seeded generator may follow later.
- Each case: true record → documents (invoices, contracts, emails, spreadsheets as text, adjuster reports)
  with planted values → current values perturbed per field (`correct`, `missing`, `wrong`,
  `correct_format_variant`) → expected output derived by construction.
- Difficulty tags: `conflict`, `restated_old_value`, `format_variant`, `ocr_noise`, `long_bundle`,
  `no_evidence` (the bundle says nothing about some or all fields), `distractor` (values that belong to
  another policy, party or period appear in the bundle).
- Document types used are those of the taxonomy (§3); invoices, spreadsheets-as-text and similar are `other`.
- Expected output is what a correct reviewer would accept, i.e. `reconcile` applied to the **true record**
  and the current values. For `restated_old_value` cases that is the single correct suggestion; a conflict
  returned by the system there counts as a false conflict (the accepted limitation of §7.3, made visible).
- Initial size ≈ 60 cases: 4 `(lob, business_type)` combinations × 2 modes × difficulty mix.

### 14.2 Metrics
Field-level matching on normalised values; free text via a pluggable `TextComparator` port
(deterministic first: chrF by default, BLEU and token-F1 available; `LLMJudgeComparator` later via pydantic_evals).

- Primary: suggestion **recall** (headline), suggestion precision, **false-change rate**.
  - Recall = expected suggestions returned with the expected value ÷ expected suggestions.
  - Precision = returned suggestions that are expected (same field and value) ÷ returned suggestions.
  - False-change rate (update mode only) = active fields whose current value is correct (`correct` or
    `correct_format_variant`) **and** for which a `change` suggestion is returned ÷ active fields whose
    current value is correct. Conflicts on such fields are not counted here; they count in the
    false-conflict rate.
- Conflicts: recall, precision, false-conflict rate (on `restated_old_value`).
- Evidence: document hit rate, `value_not_in_quote` flag rate.
- Operational: retry rate and dropped-candidate rate by cause, truncation rate, latency p50/p95, tokens per request.
- All sliced by `(lob, business_type)`, mode, group, field type, difficulty tag. Every sliced figure is
  reported with its numerator and denominator (slices are small).
- Output: pydantic_evals report + JSON summary per run (for future baselines). The summary records what is
  needed to reproduce the run: dataset version, catalog version, prompt version, model name/provider and
  generation settings (temperature, max tokens, output mode), matcher and parsing thresholds, context budget.
- Gating: **off for now**. Later gates: recall floor, false-change ceiling, retry-rate ceiling, thresholds
  set from the first baseline.

## 15. Data

Synthetic data only. Prompt/completion capture off by default regardless.

## 16. Parked (do not implement until decided here)

- Bus semantics and target broker (§9.4).
- Cross-field validation rules.
- Formatting and polishing stage.
- Hybrid retrieval selector (designed in §7.1, not scheduled).
- `ClassifyBundle` (inferring LoB / business type).
- `record` field type.
- Eval gating thresholds; LLM-as-judge comparator.
- Production observability backend.

## 17. Change process

1. Propose the change to the user; get explicit approval.
2. Update this file in the feature branch, in the same PR as the code relying on it.
3. Bump `catalog_version` / `prompt_version` when the catalog or prompts change.
