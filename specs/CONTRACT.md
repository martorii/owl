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
| **Job** | One submitted request and its state (`queued`, `running`, `done`, `failed`), identified by `job_id`. |
| **Callback** | The signed HTTP POST that delivers a finished job to the caller's `callback_url`. |
| **Profile** | The deployment variant: `databricks` or `local` (§9.2). |

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
    {"document_id": "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b", "filename": "slip.pdf", "document_type": "contract", "document_date": "2026-02-14", "text": "..."}
  ],
  "current_values": {"policy_inception_date": "2026-01-01"}
}
```

- `lob` and `business_type` are **required**. No inference (a `ClassifyBundle` step may be added later).
- `documents` must be non-empty; each `text` must be non-blank. `document_type` is **required** (use `other`
  when unknown).
- `document_date` is **required**: the ISO date (`YYYY-MM-DD`) of the document itself (letter or email date,
  effective date), not an upload date. Missing or malformed → `422`.
- `document_id` is **required**: the document's UUID, supplied by the caller. It is the only identifier of a
  document and is what evidence returns. Duplicate or malformed UUIDs → `422`.
- `current_values` is required and may be empty in `update` mode; it is not accepted in `extraction` mode.
- `current_values` keys must be catalog field names; unknown names → `422` **(default)**. Fields not active
  for the request are ignored with a warning **(default)**. Values are parsed with the field's type
  parser; unparseable values → `422` **(default)**. A `null` value means "no current value": it is treated as
  absent, without a warning.

### 5.2 Internal short IDs

- Documents are sorted **chronologically, oldest first and latest last** (stable: same date keeps input
  order). This is done by the domain bundle, so everything downstream sees the sorted order.
- In prompts, documents are labelled `D1, D2, …` (in that chronological order, so a higher number is never an
  earlier document) and active fields `F1, F2, …` (catalog order). Each document is shown with its date.
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
- A conflict is reported whenever ≥ 2 distinct normalised values remain after supersession (§7.3), **even if
  one equals the current value**.
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
  `field_inactive_ignored`, `candidate_superseded`.

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
  1. Order all documents for *inclusion priority*: first those whose type is in any of the group's
     `preferred_document_types`, then the rest; within each tier, newest first (so that the documents the
     budget drops are the oldest). No document is excluded by type alone (recall over precision).
  2. Add whole documents in that order until the token budget is reached. The included documents are then
     presented to the model in chronological order (§5.2), not in inclusion order.
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
- The documents are presented oldest to latest, each with its date. The model is instructed to report every
  distinct value it finds, with one exception: when a **later** document explicitly restates, amends or
  replaces a value from an earlier one, it reports only the later value. Where documents merely disagree,
  with no sign of supersession, it reports both. (The domain guardrail of §7.3 backs this up.)

### 7.3 Reconciliation (domain, deterministic)

Per field:
1. Normalise candidate values; merge candidates with equal normalised values (union evidence, max confidence).
2. One distinct value ≠ current → suggestion (`add` if current is absent, else `change`).
3. One distinct value = current → nothing.
4. ≥ 2 distinct values → conflict.

Supersession guardrail, applied before steps 2–4: a candidate value's *date* is the latest date among the
documents in its evidence. A value whose date is **older** than that of another value for the same field is
superseded and dropped, with a `candidate_superseded` warning. Values tied for the latest date remain and are
reconciled as above (a tie is a conflict). Consequences: an older value equal to the current value does not
prevent a `change`; a latest value equal to the current value produces no suggestion. A value that a later
document merely restates keeps that later date. Known risk: a wrong later document hides a correct earlier one
(the warning makes this visible).

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

A request can run for several minutes (6–10 in the worst case), so no HTTP call waits for the pipeline.
The caller submits a **job**, receives a `job_id` at once, and gets the result by **callback** (§9.3) or by
polling. The caller is a web application.

### 9.1 HTTP (FastAPI): job API

- `POST /v1/jobs` — submit a job.

  ```json
  {
    "mode": "update",
    "callback_url": "https://webapp.example/owl/callback",
    "input": {"lob": "property", "business_type": "reinsurance", "documents": [], "current_values": {}}
  }
  ```

  - `mode` ∈ {`update`, `extraction`}, required. `input` is the request of §5.1 for that mode.
  - `callback_url` is optional **(default)**; without it the caller polls.
  - The request is validated in full (§5.1, including parsing of `current_values`) **before** a job is
    created. An invalid request, or a `callback_url` that is not allowed (§9.3), → `422` and no job exists.
  - Response `202`: `{"job_id": "…", "status": "queued"}`. `job_id` is a UUID generated by the API and is
    also the `request_id` of the result (§6).
- `GET /v1/jobs/{job_id}` — the job's state; unknown id → `404`.

  ```json
  {
    "job_id": "…", "mode": "update", "status": "running", "progress": "3 of 6 groups done",
    "result": null, "error": null, "callback_status": "pending",
    "created_at": "…", "updated_at": "…"
  }
  ```

  - `status` ∈ {`queued`, `running`, `done`, `failed`}. `progress` is free text for display.
  - `result` is the output of §6, present only when `done`. `error` is `{"code", "message"}`, present only
    when `failed`.
  - `callback_status` ∈ {`not_requested`, `pending`, `delivered`, `failed`}.
- `GET /healthz` — liveness. `GET /readyz` — the job store is reachable **(default)**.
- There are no synchronous suggestion endpoints and no streaming (SSE/WebSocket): progress is read by polling.
- API request/response models are separate DTOs mapped to/from domain objects.
- Errors:

  | Situation | HTTP | Job | Library |
  |---|---|---|---|
  | Invalid request (§5.1) or disallowed `callback_url` | `422` | none created | `InvalidRequestError` |
  | **Every** group failed (e.g. the model is unreachable) | — | `failed`, code `extraction_failed` | `ExtractionFailedError` |
  | Job timeout exceeded | — | `failed`, code `timeout` | `ExtractionTimeoutError` |
  | Any other error | — | `failed`, code `internal` | — |

  A `done` job with empty `suggestions` and `conflicts` therefore always means "nothing to change", never
  "extraction did not run". If at least one group succeeds the job is `done` and the failed groups are
  reported as `group_failed` warnings. On timeout no partial result is returned.

### 9.2 Job execution

- Application ports: `JobStore` (create / read / update a job), `JobLauncher` (start the run of a job),
  `CallbackSender` (deliver the result). The use case `RunJob` is the same in every deployment:
  1. Set `running`; write `progress` as groups complete.
  2. Run the pipeline (§7) through the service functions (§9.4).
  3. Write `result` + `done`, or `error` + `failed`.
  4. Send the callback (§9.3) and record `callback_status`.
- `RunJob` is idempotent on `job_id`: a job already `done` or `failed` is not run again (only a callback still
  `pending` is sent); a job found `queued` or `running` is run from the start.
- Job record: `job_id`, `mode`, `status`, `progress`, `input`, `result`, `error`, `callback_url`,
  `callback_status`, `created_at`, `updated_at`, `run_id` (the launcher's reference, if any).
- The input is passed to the run through the job store, never as job parameters (only `job_id` is).
- Two deployment profiles, chosen by settings; the API, the use case and the job-store adapter are the same:

  | | `databricks` | `local` (Docker) |
  |---|---|---|
  | API | Databricks App | container |
  | Launcher | Databricks Jobs API `run-now`, as a service principal | in-process asyncio task, no queue |
  | Runner | Databricks Job (serverless, Python script task) | the API process |
  | Job store | Lakebase (Postgres) | Postgres container |
  | Model | Azure OpenAI | LM Studio (`openai_compatible`) |
  | Secrets | Databricks secrets, exposed as `OWL_` settings | `.env` |

- Databricks Job: `max_concurrent_runs` = `OWL_JOBS__MAX_CONCURRENT_RUNS` with queueing enabled, so extra
  runs wait; job timeout `OWL_JOBS__TIMEOUT` (configurable; it must cover time spent waiting on the rate
  limit, §9.5); job-level retries for transient errors. Clients never get access to the Jobs API.
- On Databricks the pipeline is packaged as an **MLflow pyfunc model** registered in Unity Catalog and loaded
  by alias (`models:/<catalog>.<schema>.<model>@<alias>`). The catalog and the prompts are model artifacts.
  The model is a thin wrapper over the service functions: it holds no pipeline logic of its own.
- Retention: `OWL_JOBS__RETENTION_DAYS` exists (default unset = keep everything). **Purging is not
  implemented yet** (§16); the job store therefore keeps document text and results indefinitely (§15).

### 9.3 Callback (webhook)

- Sent only if the job has a `callback_url`, and only **after** the result is written to the job store, so a
  failed delivery loses nothing.
- `POST` with body `{"job_id", "status", "result" | "error"}` **(default: the full result is included)**.
- Headers: `X-OWL-Job-Id`, `X-OWL-Timestamp` (Unix seconds), `X-OWL-Signature: sha256=<hex>`, the
  HMAC-SHA256 of `"<timestamp>.<body>"` with the shared secret `OWL_CALLBACK__SECRET`.
- Delivery may repeat: the receiver dedupes on `job_id` and should reject old timestamps.
- Retries: exponential backoff, 5 attempts **(default, configurable)**; any `2xx` is `delivered`; when the
  attempts are used up `callback_status` is `failed` and the job itself stays `done`/`failed`.
- `callback_url` must be HTTPS and its host must be on the allowlist `OWL_CALLBACK__ALLOWED_HOSTS`; this is
  checked by the API at submission. Plain HTTP can be allowed by a setting for the `local` profile only.
- Assumption for now: the web application's callback endpoint is reachable from Databricks.

### 9.4 Python library
```python
suggest_updates(request) -> SuggestionResult                 # sync wrapper
await asuggest_updates(request) -> SuggestionResult          # async core
await asuggest_updates_many(requests, max_concurrency=4) -> list[SuggestionResult | Failure]
```
(and the equivalent `extract_fields` / `aextract_fields` / `aextract_fields_many` for extraction mode)

Used by `RunJob`, the MLflow model, the CLI and the evals. These functions raise the library errors of
§9.1; in the `*_many` functions the errors are returned as `Failure` items instead of being raised. The
service functions accept an optional progress callback, which `RunJob` uses to write `progress`.

### 9.5 Concurrency and rate limits

- asyncio only (no threads).
- **Within a process**: one limit on concurrent LLM calls, shared by everything running in that process.
- **Across jobs**: `databricks` — `max_concurrent_runs` with queueing (§9.2); `local` — a limit on jobs
  running at once in the API process (same setting), the rest wait.
- **Tokens per minute.** The LLM deployment has a TPM quota; a request must never fail because of it.
  Waiting is acceptable.
  1. A rate-limit response (`429`) is never a failure: the call waits (`Retry-After` if given, otherwise
     exponential backoff with jitter) and is retried, up to a total waiting budget per call
     (`RATE_LIMIT_MAX_WAIT`, configurable). These waits do not count against `MAX_RETRIES` or against the
     `ModelRetry` budget of §8.4. Only when the waiting budget is used up does the call fail (and with it
     the group, §8.4).
  2. `TPM_LIMIT` is an optional per-role setting (the quota is not known yet). When set, each process paces
     its LLM calls so that its estimated tokens per minute (`TokenCounter` estimate of the prompt +
     `MAX_TOKENS`) stay within `TPM_LIMIT ÷ OWL_JOBS__MAX_CONCURRENT_RUNS`. Runs therefore share the quota
     statically, without shared state. When unset, only rule 1 applies.
- A shared token bucket across runs (in the job store) is parked (§16).

## 10. LLM and models

- Settings via pydantic-settings (env / `.env`), prefix `OWL_`, nested delimiter `__`.
- Models configured **per role**: `extraction` (now), `polishing`, `judge` (later). Unconfigured roles fall
  back to `default`.
- Per role: `PROVIDER` ∈ {`openai_compatible`, `azure_openai`, `openai`}, `NAME`/`DEPLOYMENT`, `BASE_URL` /
  `AZURE_ENDPOINT` / `API_VERSION`, `API_KEY` (SecretStr), `CONTEXT_WINDOW`, `TEMPERATURE` (default 0),
  `MAX_TOKENS`, `TIMEOUT`, `MAX_RETRIES`, `OUTPUT_MODE` ∈ {`native` (default), `tool`, `prompted`}.
- A single `ModelFactory` (infrastructure) maps settings → pydantic_ai `Model`. No other module knows the provider.
- Providers by profile (§9.2): `databricks` uses Azure OpenAI (`azure_openai`); `local` uses LM Studio via
  `openai_compatible`. On Databricks the secrets (LLM key, callback secret, job-store credentials) live in
  Databricks secrets and reach the code as `OWL_` settings.
- Rate limiting per role (§9.5): `TPM_LIMIT` (optional, unset by default) and `RATE_LIMIT_MAX_WAIT`.
- Token counting: `TokenCounter` port. Default: characters ÷ 3.5 with a 15% safety margin. `tiktoken`
  counter available for OpenAI/Azure.
- Prompts are versioned Jinja templates in `src/owl/infrastructure/llm/prompts/`; `prompt_version` is recorded.

## 11. Observability

- **MLflow Tracing** is the only tracing backend. No OpenTelemetry Collector, no separate metrics backend.
  No tracking URI configured → no-op.
- Spans: `pipeline.select_fields`, `pipeline.select_context`, `pipeline.extract`, `pipeline.validate`,
  `pipeline.reconcile` (per group where relevant), under one root span per job carrying `job_id`,
  `catalog_version` and `prompt_version`. LLM spans via MLflow's pydantic_ai integration.
- Prompt/completion and document content capture is off by default (`OWL_TRACING__CAPTURE_CONTENT=false`);
  the adapter must make sure span inputs/outputs are not recorded when it is off.
- Counts that were planned as metrics (LLM calls, tokens, retries, rate-limit waits, dropped candidates by
  reason, truncations, skipped documents) are recorded as attributes on the spans.
- Logs: structured JSON (`structlog`) carrying `job_id` and the trace id.
- The domain and application layers have no MLflow dependency. The application emits spans through a
  `Telemetry` port (`span(name, attributes)` context manager, `set_attributes`); the default adapter is a
  no-op and the MLflow adapter lives in infrastructure. Application code logs through stdlib `logging`;
  `structlog` is configured in infrastructure as the renderer.
- Local stack (docker-compose): app + Postgres, optional MLflow tracking server.

## 12. Architecture

DDD, layered, enforced with import-linter:

```
src/owl/
  domain/          pure Python + pydantic + rapidfuzz (pure computation); no I/O, no pydantic_ai, no MLflow,
                   no FastAPI
  application/     ports (Protocols), use cases (pipeline, SubmitJob, RunJob), service functions
  infrastructure/  pydantic_ai agent, ModelFactory, prompts, context selectors, job store, job launchers,
                   callback sender, MLflow model wrapper, YAML catalog loader, telemetry, settings
  entrypoints/     FastAPI app, job runner, CLI
catalog/fields.yaml
deploy/databricks/ job, App and secret-scope definitions
evals/             datasets/, comparators, runner, reports
tests/             unit/, pipeline/, api/, contract/
```

Import rules:
- `domain` imports nothing from `application`, `infrastructure`, `entrypoints`.
- `application` imports only `domain`.
- Only `infrastructure` imports `pydantic_ai`, `mlflow`, the Databricks SDK, the Postgres driver, `httpx`,
  `yaml`.
- `entrypoints` wires everything together (composition root).

## 13. Quality, CI and CD

- Tooling: Python 3.12, uv, ruff (lint + format), pyright (strict on `domain/` and `application/`),
  pytest, import-linter, pre-commit, Dependabot (uv + actions), Conventional Commits.
- CI (GitHub Actions, every PR and push):
  `uv sync --frozen` → ruff → pyright → pytest (coverage ≥ 85% on domain + application) → import-linter →
  Docker build + Trivy scan (report-only) → eval job (**present but disabled**: `workflow_dispatch` only).
- Tests never call a real LLM. Pipeline tests use pydantic_ai `FunctionModel`/`TestModel`.
- CD: merge to `main` → push image to GHCR as `ghcr.io/<owner>/owl:sha-<short>` and `:main`;
  tag `vX.Y.Z` → `:X.Y.Z` + `:latest` + GitHub Release. The image serves the `local` profile.
- Databricks (`databricks` profile): the job, the App and the model registration are defined under
  `deploy/databricks/` and deployed by the user by hand for now **(default)**; CI does not deploy.
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
- Initial size 64 cases (`evals/datasets/v1/`): 4 `(lob, business_type)` combinations × 2 modes × 8 cases; in each
  of these 8 cells every difficulty tag appears at least once. Two cases are fully silent (no evidence at all).
- Case format: `evals/schema.py` (`GoldenCase`, JSON schema in `case.schema.json`), checked by
  `evals/validate_dataset.py`. Documents carry a `document_date` and are stored oldest first (§5.2).
  `true_record` holds the correct value of a subset of the active fields (not all of them).
  `planted` lists every quote with a role: `true`, `conflicting`, `superseded` (older value in an
  earlier-dated document; the supersession guardrail of §7.3 must drop it), `restated_old` (an old value
  restated in the same sentence as the new one) or `distractor` (another policy, party or period).
- `expected` is `reconcile` applied to the planted `true`, `conflicting` and `superseded` candidates and the
  current values. It is stored as values only (no evidence), since metrics compare field and value. The
  validator recomputes it and also checks it against an independent rule (missing → add, wrong → change,
  correct → nothing, conflicting planted → conflict).
- A planted `conflict` is two quotes with the **same document date** (a tie is a conflict, §7.3); a value in
  a later document would supersede the other and is therefore not a conflict case.
- `wrong` current values are plausible values of the same type; `correct_format_variant` is a differently
  spelled value that the domain parser reads as the true one.
- Documents are English. `long_bundle` means ≥ 8 documents (about 3,000 words); there is no separate size
  threshold.
- The files of a version directory are frozen once accepted (a checksum test enforces it); a regeneration
  goes into a new directory (`v2/`).

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

The job store keeps each job's `input` (full document text) and `result` (verbatim quotes), and the
callback carries the result. Before real documents are processed, retention (§9.2), access to the job
store and per-caller access to `GET /v1/jobs/{job_id}` must be decided here.

## 16. Parked (do not implement until decided here)

- Job retention purge (the setting exists, §9.2) and the other conditions for real data (§15).
- API authentication and per-caller job access.
- Shared token bucket across runs (§9.5); live progress streaming (SSE/WebSocket).
- An external message broker: not needed at this volume; Databricks Jobs provide queueing and retries.
- Cross-field validation rules.
- Formatting and polishing stage.
- Hybrid retrieval selector (designed in §7.1, not scheduled).
- `ClassifyBundle` (inferring LoB / business type).
- `record` field type.
- Eval gating thresholds; LLM-as-judge comparator.

## 17. Change process

1. Propose the change to the user; get explicit approval.
2. Update this file in the feature branch, in the same PR as the code relying on it.
3. Bump `catalog_version` / `prompt_version` when the catalog or prompts change.
