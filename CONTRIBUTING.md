# Contributing

Small, disciplined contributions land fast here. The bar is defined by three documents: `AGENTS.md` (project rules and invariants), `docs/requirements.md` (every behaviour has a testable requirement), and `docs/plan.md` (what is in flight).

## Ground rules

- **Python 3.10+ standard library only.** No runtime dependencies, ever. Dev tooling installs with `pip install -e .[test]` (pytest is the only dev dependency, declared in pyproject). Optional layers (like embeddings) must fail soft when their backend is absent.
- **Hooks never block a session.** Every hook swallows every error and exits 0. This is test-covered and non-negotiable.
- **Supersession over mutation.** Corrections insert a new row pointing at the old one; nothing rewrites history in place.
- **Schema changes ship as migrations** (`db.MIGRATIONS`, ordered, transactional, idempotent) and update `docs/data-architecture.md` in the same commit, including the Mermaid diagrams (render-test them).
- **Every behaviour change lands with a test.** The suite must be green (`python -m pytest tests/ -q`) before merge; CI enforces it across Python 3.10/3.12 on Linux and Windows.
- No em or en dashes in any file. Plain readable text in the store, never blobs.

## Workflow

1. Open or pick an issue; the templates ask for what we actually need. Check `docs/roadmap.md` first, including the deliberately-not-planned list.
2. Branch from `main`: `feature/short-name` or `fix/short-name`.
3. Implement with tests; update the docs the change touches (requirements matrix, use cases, changelog) in the same branch.
4. Push and open a PR. Describe the requirement IDs the change satisfies.

## Privacy in issues and PRs

Memory stores hold personal data. Never paste real memory content, secrets, or store files into an issue, PR, or test fixture; use synthetic examples. `*.db` files are gitignored, keep it that way.
