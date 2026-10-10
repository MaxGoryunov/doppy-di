# Issue 140 — Analyzer findings not fixable under fast-path rules

Scope: `providers.py`, `inject.py`, `cli.py`, `plan.py`, `container.py`
(Scrutinizer F-score modules).

Rules (from #140 discussion): no change to compiled-plan (fast path)
functionality — no new branches, no extra function calls on the resolve
path, no function-body splits, no shipped-signature changes. Stylistic
changes (renames, type hints, comment/NOSONAR fixes, cold-path-only
refactors) are allowed. Everything already fixed that way is NOT listed
here — only what remains and why.

Data snapshot: SonarCloud `main` 47 unresolved (2026-10-10);
Scrutinizer branch head index `10706853` (inspection `8359c08e`,
head `2a463232`) 119 issues; qlty `smells --all` local output
(same population Cloud grades on). Note: `providers.py:119`
(`candidate`), `providers.py:232/263` (dup) in that index are already
fixed locally by commit `268fe88` (pushed, awaiting next scan).

## SonarCloud

### S6546 `Union[]` → `|` — 29 total, 27 in scope (MAJOR)

- `src/doppy_di/providers.py:70,80,97,186,187,217,218,250,251,291,314,360,433,478,510,512,583,611,634,657`
  (20 incl. 2 from new helpers at :111/:123)
- `src/doppy_di/container.py:1243,1271,2266,2465` (4)
- `src/doppy_di/inject.py:52,58` (2)
- `src/doppy_di/auto_wiring.py:170` (1, out of scope but same cause)
- Why not fixable: touches 29 shipped signatures; `pyproject.toml`
  sets `target-version = "py39"` and explicitly ignores `UP007`;
  PEP 604 `X | Y` in annotations breaks the declared floor.
  Project policy wins over the smell. Revisit only by dropping py39.

### S7497 re-raise `CancelledError` — 1 (BUG, MAJOR)

- `src/doppy_di/container.py:2160` — `except asyncio.CancelledError`
  translates to `ResolutionCancelledError` instead of re-raising.
  Pre-existing (created 2026-08-11).
- Why not fixable here: cancellation translation is a semantic
  decision on the resolve path (fast-path behavior). Needs its own
  issue with a behavior contract, not a style fix.

### S7504 redundant `list()` — 1 (MINOR)

- `src/doppy_di/plan.py:1433` — `list(ruleset.map.items())`.
- Why not fixable: the loop body calls `ruleset.add`, mutating the
  map during iteration. Removing `list()` changes behavior
  (RuntimeError / skipped entries). The snapshot is load-bearing.

### S5778 single-throw exception tests — 18 on `main` (MAJOR)

- `tests/test_inject.py:220,242,283,292,301,312,428,437,459,472`,
  `tests/test_child_containers.py:146,267`,
  `tests/test_graph_introspection.py:107,116`,
  `tests/test_parallel_resolution.py:75,230`,
  `tests/test_providers.py:237`, `tests/test_policies.py:143`.
- Why listed: test-only, techically fixable with the `run()` helper
  pattern (as done for `test_async_resolution.py`/`test_diagnostics.py`
  in `6503186`), but the async variants need `coro = ...` hoisting
  per site and were left for a dedicated test-cleanup pass.
  Not a fast-path constraint — a scope decision.

## Scrutinizer (branch head index, 119 issues)

### `sometimes_not_defined` — 103 in `plan.py` + 1 in `providers.py`

- `plan.py` emitter tables: `dep0/dep1/dep2`, `m0-m3`, `d0/d1/d2`,
  `mk0/mk1/mk2`, `a0/a1/a2`, `f1/f2/e1/e2` (lines 131–622);
  `providers.py:711` (`names`). All severity 0.
- Why not fixable: each variable is assigned on exactly one exclusive
  arity branch (`if n == 0/1/2/3/else`) and read in the shared closure.
  The analyzer cannot model branch-exclusive assignment. Renames and
  default-arg binding do not help (name is defined; control flow is the
  blind spot). The only fix is restructuring branches — forbidden.

### `not_defined` — 3 remaining (sev 5, all FPs)

- `container.py:2668` (`repr`): builtin shadowed by a docstring example;
  analyzer parses the doctest as code. Renaming falsifies docs. No fix.
- `inject.py:506` (`plan`): `nonlocal plan` cell read before first
  assignment on the analysis graph; runtime always assigns via
  `_get_plan()` first (mypy narrowing confirms). Restructure = extra
  call/branch. No fix.
- `providers.py:119` (`candidate`): fixed in `268fe88`; pending scan.

### `one_undefined_edge.for_not_entered` — 2 (sev 0, FPs)

- `container.py:2015` (`level_dep`, loop at :2011),
  `container.py:2194` (`level_key`, loop at :2193).
- Why not fixable: loop variables over `level`, never empty at those
  points (non-empty guarded; `total < 5` early return precedes).
  Renaming (`dep`→`level_dep` in `9c54992`) only moved the flag to the
  new name — proof it is name-triggered, not flow-real. Dummy inits
  would add dead stores. No fix.

### `duplicated_code` — 8 in scope (sev 1)

- `container.py:1335/1425`: sync/async `find`/`has`/`deps_of` mirrors.
  Merge needs branch or dynamic dispatch in lookup path. Forbidden.
- `inject.py:513/545`: `sync_wrapper`/`async_wrapper`. Merge needs
  `iscoroutinefunction` branch inside hot wrapper or shared helper
  call per resolution. Forbidden.
- `plan.py:344/354`: `_lit_*` emitter variants vs `_legacy.py` frozen
  baseline. Intentional (byte-comparable for perf work). Forbidden.
- `providers.py:232/263`: reshaped in `268fe88`; pending scan.
- `devkit/policy.py:76/115`: protocol hook overloads (same reason).

## qlty smells (structural; Cloud grade drivers)

Local `qlty smells --all`; `[[triage]]` for `_legacy.py`,
`benchmarks/`, `scripts/` is Cloud-side (committed, local CLI still
lists them — known limitation, not a code issue).

- `plan.py` (complexity 311): returns 6–16 across 11 emitters;
  params (`_finish_node` 7, `_refine_resolvers` 6); 3 complex
  binaries; 10 dup blocks vs `_legacy.py`. Any fix = split, branch,
  or call in compiled plan. Forbidden.
- `container.py` (complexity 358): params 6–7 across 8 resolvers
  (`_resolve_uncached`, `_store_result`, `_resolve_async_body`,
  `_run_async_yield`, `aget`, `service` x2, `__init__`). Fix =
  breaking signature change or resolve-path allocation. Forbidden.
- `inject.py` (complexity 86): params 7/8/8, returns 8/7. Same block.
- `providers.py` (complexity 51): `implicit_collection_rule`
  8 returns — cold path, deferred as churn-for-smell-only.
- `cli.py` (complexity 82): click-dispatch boilerplate, no function
  findings. Split = CLI surface risk. Forbidden.
- `_legacy.py` (323): frozen baseline, must not be refactored.
- `benchmarks/`, `scripts/`: not shipped code, triaged.

## What would unlock these (out of scope for #140)

1. Drop py39 floor → `|` hints close 29x S6546 mechanically.
2. Benchmark-gated emitter surgery (one emitter per PR) → plan
   returns/complexity/dup.
3. Public-API major version → resolver/injection signatures.
4. Dedicated S7497 issue with cancellation contract → the 1 BUG.
5. Test-cleanup pass with `run()`/`coro` hoisting → 18x S5778.

