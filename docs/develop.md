# Saidkick Development Guide

This guide is for developers and AI agents contributing to the Saidkick project.

## Development Workflow

1.  **Branching**: Use feature branches for any new development.
2.  **Versioning**: Follow Semantic Versioning (SemVer).
3.  **Linting & Formatting**: Use **Ruff** for all Python code. The repository includes a `.ruff.toml` with the required configuration.
    ```bash
    uv run ruff check .
    uv run ruff format .
    ```

## Testing Strategy

Saidkick uses **Pytest** for all levels of testing.

### 1. Unit & Integration Tests
Located in `tests/test_saidkick.py` and `tests/test_saidkick_enhanced.py`. These tests use `FastAPI.testclient` and mock asynchronous command execution.
- Run them with:
  ```bash
  uv run pytest -m "not e2e"
  ```

### 2. End-to-End (E2E) Tests
Located in `tests/test_saidkick_e2e.py`. These tests launch a real Chrome instance, load the extension, and perform a full integration test against a local test page.
- Note: These tests require a graphical environment (or a tool like `xvfb`).
- Run them with:
  ```bash
  uv run pytest -m "e2e"
  ```

## Project Standards

### Code Style
- **Python**: Follow PEP 8. Use clear, descriptive variable and function names.
- **JavaScript**: Use ES6+ syntax. Avoid external libraries in the extension to keep it lightweight.

### Documentation
- All new features must be documented in the `docs/` directory.
- Update `user-guide.md` for any changes to the CLI or programmatic API.

## Contribution Guidelines

- **Atomic Commits**: Keep commits focused and logically separated.
- **Tests**: Every new feature or bug fix must include corresponding tests.
- **Documentation**: Ensure the documentation remains accurate and up-to-date.
