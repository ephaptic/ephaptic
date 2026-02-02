# Contributing to Ephaptic

First off, thank you for considering contributing to Ephaptic. This project is a labor of love, and we appreciate anyone willing to help make it better.

## A Note on AI Tools & Vibe Coding

We embrace technology, but **we have strict regulations on Vibe Coding.**

If you use an agent (Codex, Claude, Copilot, Antigravity) to generate code:
1.  **You must understand every line you submit.**
2.  **You must test it manually.**
3.  **Do not change anything outside the scope** - a common issue with AI.

**PRs that appear to be raw, untested AI slop will likely be closed immediately.**

## Development Setup

Ephaptic is a monorepo containing both Python and JavaScript packages.

1.  **Clone the repo:**
    ```bash
    $ git clone https://github.com/ephaptic/ephaptic.git
    $ cd ephaptic
    ```

2.  **The Golden Rule:**
    Before submitting a PR, you should run the test suite. We have a unified script that handles Python unit tests, JS unit tests, and full integration tests.
    ```bash
    $ ./tests.sh
    ```
    If you don't run the test suite, GitHub Actions will when the PR is open.

## Did you find a bug?

*   **Ensure the bug was not already reported** by searching on GitHub under [Issues](https://github.com/ephaptic/ephaptic/issues).
*   If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/ephaptic/ephaptic/issues/new).
*   **Be specific:** Include your Python version or Node/Bun/Deno version, and a minimal reproduction script if possible.

## Submitting a Pull Request

1.  **Scope:** Keep changes focused. If you are fixing a bug, do not also reformat the entire codebase.
2.  **Type Safety:**
    *   **Python:** All new functions must have type hints. Pydantic models should be used for complex data.
    *   **TypeScript:** `any` is strictly discouraged. Update the schema generation if you change backend types.
3.  **Architecture:**
    *   Ephaptic uses a **Transport Agnostic** architecture. Do not hardcode logic into `ephaptic.py` that belongs in an adapter or transport layer.
    *   Respect the **Invisible Middleware** philosophy. The user should barely know Ephaptic is there.

## Cosmetic Changes

Changes that are purely cosmetic (whitespace, formatting, variable renaming) and do not add to the stability, functionality, or testability of Ephaptic will generally not be accepted.

## Coding Conventions

These do not have to be followed, but following them will boost your chances of your PR being merged.

- **String quotations:**
  - For strings that end up being output seen by the user; e.g. logs: Use `"`.
  - For strings that are purely internal; e.g. dictionary keys: Use `'`.
- **Conventional commits:**
  - Ensure to use [Conventional Commits](https://www.conventionalcommits.org/) when committing changes in the PR.
- **Testing:**
  - Test your code frequently to catch bugs before they accumulate.
  - Tests can be ran simply by running the `./tests.sh` script locally.
  - Tests are also ran by Actions upon opening a PR.
