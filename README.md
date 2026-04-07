# 🦉 OWL (Omniscient Workflow Ledger)

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

**OWL** is a modern, high-performance Intelligent Document Processing (IDP) and RAG pipeline designed specifically for complex claim document ingestion and analysis. It turns chaotic claim diaries and ledgers into structured, audit-ready intelligence.

---

## ✨ Key Features

- **🚀 Automated Ingestion Pipeline**: End-to-end processing from raw document blocks to structured summaries.
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
| **[Mypy](https://mypy-lang.org/)** | Static Type Checker | Guarantees type safety across the knowledge base. |
| **[Pytest](https://pytest.org/)** | Testing Framework | Robust testing for parsing and retrieval logic. |

---

## 📂 Project Structure

```text
owl/
├── src/
│   └── tools/
│       ├── knowledge_base/   # Ingestion & RAG logic
│       ├── llm/              # LLM generation & prompting
│       └── parser/           # Document parsing & OCR filtering
├── tests/                    # Comprehensive test suite
├── debug/                    # Debugging scripts & processed outputs
├── pyproject.toml            # Project & tool configuration
└── README.md
```

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
*   **Linting**: `uv run ruff check .`
*   **Type Check**: `uv run mypy src`
*   **Formatting**: `uv run ruff format .`

---

## 💡 Philosophy

OWL is built on the principle of **Precision-First Retrieval**. In the domain of claim processing, a missed detail can be costly. We prioritize:
1.  **High-Fidelity Extraction**: Tables and text are treated with equal importance.
2.  **Contextual Depth**: Using parent-child chunking to maintain document hierarchy.
3.  **Auditability**: Every generated summary links back to its source evidence.
