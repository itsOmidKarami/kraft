# Kraft dev tasks. Run `just` to list.

set shell := ["bash", "-uc"]

_default:
    @just --list

# Install backend + frontend deps
setup:
    uv sync
    cd frontend && npm install

# Run backend only (127.0.0.1:8765)
api:
    uv run python -m kraft

# Run frontend dev server only (localhost:5173, proxies to backend)
ui:
    cd frontend && npm run dev

# Backend + frontend dev servers together (Ctrl-C stops both)
dev:
    #!/usr/bin/env bash
    set -uo pipefail
    trap 'kill 0' EXIT
    uv run python -m kraft &
    cd frontend && npm run dev &
    wait

# Build the SPA and serve everything from the backend on :8765
start:
    cd frontend && npm run build
    uv run python -m kraft

# Backend tests (add args, e.g. `just test -k search`)
test *ARGS:
    uv run pytest {{ARGS}}

# Frontend unit tests
test-ui:
    cd frontend && npm test

# Playwright e2e
e2e:
    cd frontend && npm run e2e

# Lint + format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Autofix lint + format
fix:
    uv run ruff check --fix .
    uv run ruff format .
