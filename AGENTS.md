# Repository Guidelines

## Project Structure & Module Organization
`reader3.py` handles EPUB and TXT ingestion, parsing chapters into `data/<book>_data/` folders that cache pickled `Book` objects plus extracted images. `server.py` hosts the FastAPI app and renders `templates/library.html` and `templates/reader.html` through Jinja2. `books/` is the watched inbox for raw `.epub`/`.txt` uploads (via drag-and-drop or manual copies); anything inside is converted to `data/<book>_data/` on demand. Keep large book samples or generated datasets out of the repo; only commit reproducible code, templates, and small fixtures.

## Build, Test, and Development Commands
- `uv sync` installs the locked dependencies from `uv.lock` and respects the Python 3.10+ requirement in `pyproject.toml`.
- `uv run reader3.py path/to/book.epub` (or `.txt`) ingests a book and materializes `data/<title>_data/`; TXT parsing auto-detects encodings via `charset-normalizer`. Rerun whenever you update parsing logic to refresh cached pickles. Pass `--library <dir>` to override the `data/` root.
- `uv run server.py` starts the local server on http://localhost:8123; it hot-reloads templates but not Python modules, so restart after backend changes. The server also watches `books/` (configurable via `READER_UPLOAD_DIR`) and auto-ingests new EPUB/TXT files on the next library refresh or via the `/upload` endpoint.

## Coding Style & Naming Conventions
Use Python 4-space indentation, prefer f-strings, and keep functions under ~50 lines by extracting helpers (mirroring the current `process_epub` structure). Maintain descriptive dataclass names (`BookMetadata`, `TOCEntry`) and snake_case module-level functions. Run `uv run python -m compileall reader3.py server.py` before committing if you touch parsing internals to catch syntax errors quickly; format with `ruff format` or `black` if already installed, otherwise keep diffs minimal and PEP 8 compliant.

## Testing Guidelines
No automated suite exists yet, so rely on manual regression checks: ingest a sample EPUB (e.g., `tests/fixtures/dracula.epub` if you add one) and confirm navigation, TOC links, and image rendering in the browser. Name any future tests `test_<feature>.py` under `tests/` and run them via `uv run pytest`. When touching parsing logic, include a tiny EPUB or HTML fragment test case that exercises toc parsing, anchor rewriting, and caching.

## Commit & Pull Request Guidelines
The existing history uses short imperative subjects (“first and last commit”); follow that pattern, keep bodies wrapping at 72 chars, and reference issues with `Fixes #NN` when applicable. PRs should summarize the change, list manual verification steps (commands above), and include screenshots of the library and reader views when modifying templates. Highlight any schema changes to cached `_data` folders so reviewers can safely rebuild their local libraries, and remind them to clear the `data/` cache when necessary.
