# AGENTS

<!-- BEGIN perk managed -->
## perk conventions (managed by `perk init` — do not edit between these markers)

This repo is wired for the **perk** plan-oriented workflow on Pi.

- **`perk init` owns all Pi wiring.** Every managed piece — `.pi/settings.json`
  package entries, `.pi/workflow/` dirs, `.gitignore` entries, this block — is
  written by `perk init`. Converge any repo by (re-)running `perk init`; it is
  idempotent (a no-op on an already-converged repo).
- **`init` converges *forward*; `doctor --fix` repairs oddities.** Do not bake
  backwards-compat migrations into `init`.
- **Headless-fail-safe.** In extensions, guard every rich-UI call with `ctx.hasUI`
  and block dangerous operations when `!ctx.hasUI`.
- **GitHub access goes through the `gh` CLI.** Never fetch `github.com` /
  `api.github.com` over raw HTTPS (curl/fetch) — private repos reject
  unauthenticated requests; `gh` is already authenticated. Read-only `gh`
  query subcommands (view/list/diff/status/checks/search) work even in perk
  read-only sessions.
- **State tiers:** GitHub (canonical) / `.pi/workflow/` (cache) / session entries
  (transient). Cross-plane contracts live in `shared/`.
- **Prefer ast-grep for code search.** Use `ast-grep` (structural/AST search) over plain
  `grep` when searching for code structures or language constructs; see the `ast-grep` skill.
  Plain `grep` remains fine for literal text.

perk version: 0.0.1
<!-- END perk managed -->
