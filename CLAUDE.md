# CLAUDE.md

OWL suggests updates to insurance record fields from an OCR'd document bundle, with every suggestion
grounded in verbatim quotes. Python 3.12, DDD, pydantic_ai, FastAPI.

## Read first
- **[specs/CONTRACT.md](specs/CONTRACT.md)** — the binding decisions. Code must conform to it. Do not change a decision
  without explicit user approval; if a change is approved, update specs/CONTRACT.md in the same branch.
  Never implement items listed under "Parked".
- **[specs/PLAN.md](specs/PLAN.md)** — the step-by-step plan. Work only on the current step.

## Workflow
- One branch per PLAN step: `feat/<NN>-<slug>` from latest `main`.
- Write tests first; keep to the step's scope; record out-of-scope findings under "Follow-ups" in specs/PLAN.md.
- Gate before reporting: ruff, pyright, import-linter, pytest all green (commands below).
- Report results and deviations to the user, then **wait for acceptance before merging** and before starting
  the next step. Mark the step `[x]` in specs/PLAN.md when merged.
- Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`, `ci:`).

## Commands
```bash
uv sync                                 # install
uv run pytest                           # tests
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports                     # layer rules
uv run uvicorn owl.entrypoints.api.app:app --reload   # API (from step 08)
```

## Architecture rules (enforced by import-linter)
- `owl.domain`: pure Python + pydantic. No I/O, no pydantic_ai, no FastAPI, no OTel, no YAML.
- `owl.application`: ports (`typing.Protocol`) and use cases; imports only `domain`.
- `owl.infrastructure`: the only place that imports pydantic_ai, OTel exporters, httpx, yaml.
- `owl.entrypoints`: FastAPI/CLI; composition root that wires adapters into the application.
- API DTOs are separate from domain models.
- Business decisions (normalisation, "is this a change", conflicts) live in the domain, never in prompts or the LLM.

## Conventions
- Async-first core; sync functions are thin wrappers. asyncio only, no threads.
- Short IDs (`D1…`, `F1…`) exist only inside prompts/validation; never leak them into responses.
- Validation failures in LLM output → one aggregated `ModelRetry` message.
- Prompts are Jinja templates under `src/owl/infrastructure/llm/prompts/`; bump `prompt_version` when changed.
  Bump the catalog `version` when `catalog/fields.yaml` changes.
- Settings only via `owl.infrastructure.settings` (env prefix `OWL_`, nested `__`). No provider-specific
  code outside `ModelFactory`.
- Type hints everywhere; pyright strict on domain and application.

## Testing
- Never call a real LLM in tests. Use pydantic_ai `FunctionModel`/`TestModel` or fake ports.
  Set `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` in `conftest.py`.
- Domain: table-driven + hypothesis property tests. Coverage ≥ 85% on domain + application.
- Layout: `tests/unit/`, `tests/pipeline/`, `tests/api/`, `tests/contract/`.
- Real-LLM checks (LM Studio) and evals are run manually by the user; the CI eval job stays disabled.

## Data
Synthetic data only. Do not add real documents or personal data to the repo. The golden set lives in
`evals/datasets/` and is frozen once accepted; regenerate only on purpose, with a new version directory.
