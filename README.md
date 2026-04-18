# 🦉 OWL (Omniscient Workflow Ledger)

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with pyright](https://microsoft.github.io/pyright/img/pyright_badge.svg)](https://microsoft.github.io/pyright/)

**OWL** is a modern, high-performance Intelligent Document Processing (IDP) and RAG pipeline designed specifically for complex claim document ingestion and analysis. It turns chaotic claim diaries and ledgers into structured, audit-ready intelligence.

---

## ✨ Key Features

- **🚀 Automated Ingestion Pipeline**: End-to-end processing from raw document blocks to structured summaries.
- **🔢 Deterministic Claim Extraction**: A robust, type-safe pipeline that replaces fuzzy LLM merging with deterministic weighted aggregation of evidence.
- **📄 Advanced Parsing**: Precise text and table extraction with character-level bounding box filtering to eliminate overlapping noise.
- **🧠 Hybrid RAG Strategy**: Combines BM25 lexical search with dense vector embeddings using **Reciprocal Rank Fusion (RRF)** and **Cross-Encoder Re-ranking**.
- **📊 Structured Reporting**: Automatically generates professional `summary_table.md` reports by cross-referencing claim diaries and processing ledgers.
- **🛠 Modular Architecture**: Flexible chunking strategies (Recursive, Semantic, Parent-Child) tailored for complex legal and medical documents.

---

## 🛠 Tech Stack

| Tool | Purpose | Key Benefit |
| :--- | :--- | :--- |
| **[uv](https://github.com/astral-sh/uv)** | Package & Project Manager | Blazing fast sync, unified environment management. |
| **[Ruff](https://github.com/astral-sh/ruff)** | Linter & Formatter | Rust-based speed, ensures code quality. |
| **[Pyright](https://github.com/microsoft/pyright)** | Static Type Checker | Fast and accurate type checking for complex schemas. |
| **[Pytest](https://pytest.org/)** | Testing Framework | Robust testing for parsing and retrieval logic. |

---

## 📂 Project Structure

```text
owl/
├── src/
│   ├── prompts/              # LLM prompt templates (Extraction, Merging)
│   └── tools/
│       ├── knowledge_base/   # Ingestion, Merging & RAG logic
│       │   ├── claim_fields.py # Structured field definitions
│       │   ├── field_types.py  # Type system & normalization
│       │   └── ingester.py     # Main ingestion workflow
│       ├── llm/              # LLM generation & prompting
│       └── parser/           # Document parsing & OCR filtering
├── tests/                    # Comprehensive test suite (95%+ coverage)
├── debug/                    # Debugging scripts & processed outputs
├── pyproject.toml            # Project & tool configuration
└── README.md
```

---

## 🛡 Deterministic Extraction & Type System

OWL features a robust type system for claim fields, ensuring that extracted data is consistent, normalized, and correctly merged across multiple source documents.

### Available Types
- **StringType**: Plain text; case-insensitive equality.
- **NarrativeType**: Long free-form text; uses prefix-based heuristics for comparison.
- **DateType**: Parsed to ISO-8601 (YYYY-MM-DD).
- **CurrencyType**: Canonicalized monetary amounts (e.g., `EUR 12650`), stripping decimals and symbols.
- **EnumType**: Fixed vocabulary with support for aliases.

### Weighted Merging
The pipeline groups observations across documents and selects the best value for each field based on:
1. **Recency**: Newer documents carry more weight.
2. **Frequency**: Values corroborated by multiple sources are preferred.
3. **Confidence**: Source-specific reliability scores.

---

## 🚀 Getting Started

### 1. Prerequisites
Ensure you have [uv](https://github.com/astral-sh/uv) installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Setup Environment
```bash
# Clone the repository
git clone https://github.com/martorii/owl.git
cd owl

# Sync dependencies and create venv
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

### 3. Usage
Run the ingester on your claim documents:
```bash
uv run python src/tools/knowledge_base/ingester.py --path path/to/claims
```

---

## 🛠 Development

OWL follows strict coding standards to ensure reliability:

*   **Tests**: `uv run pytest`
*   **Coverage**: `uv run pytest --cov=src`
*   **Linting**: `uv run ruff check .`
*   **Type Check**: `uv run pyright`
*   **Formatting**: `uv run ruff format .`

---

## 💡 Philosophy

OWL is built on the principle of **Precision-First Retrieval**. In the domain of claim processing, a missed detail can be costly. We prioritize:
1.  **High-Fidelity Extraction**: Tables and text are treated with equal importance.
2.  **Contextual Depth**: Using parent-child chunking to maintain document hierarchy.
3.  **Auditability**: Every generated summary links back to its source evidence through deterministic field records.
