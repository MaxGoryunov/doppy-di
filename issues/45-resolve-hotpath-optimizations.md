# Resolve hot-path optimizations: singleton cell, direct bound dispatch, EAFP, literal2 flat templates

## Context

Follow-up on issue #44 (close the ~28% resolve-speed gap vs Injex without `exec`/codegen). This issue implemented the four proposed items on the compiled `ExecutionPlan` path and saved the pre-change implementation as `LegacyExecutionPlan` (in `_legacy.py`) for a before/after comparison.

Implemented:

1. **Singleton cell cache.** `_wrap_singleton` now reads a captured list cell (`cell[0]`) on the hot path instead of `single.get(key, _MISSING)`; the live `container.single` dict plus double-checked locking is consulted only on cache miss.
2. **Direct bound dispatch.** `BoundResolver` selects the resolver callable once at bind time (with a dynamic override/tracer re-guard) and `__call__` invokes it directly with zero dict lookups, instead of re-entering `plan.get`.
3. **EAFP dispatch.** `ExecutionPlan.get` uses `try: resolvers[lookup]` / `except KeyError: _resolve_fast` instead of `.get()` on the expected-hit path.
4. **extended flat templates.** `_emit_literal2_root` adds single-frame literal templates for transient roots whose children each pull two values from the singleton prelude.

## Benchmarks

Graph: `Settings` (singleton), `ApiClient(Settings)` (singleton), `UserRepository(ApiClient)` (transient), `EmailSender(ApiClient)` (transient), `AuditLog(Settings)` (transient), `RegisterUser(UserRepository, EmailSender, AuditLog)` (transient), root `RegisterUser`. Median µs/op, GC off, warmup 150k, 400k iterations × 11 samples.

| Implementation | Median µs/op |
|---|---|
| manual | 0.801 |
| injex.resolve | 1.065 |
| legacy plan.get | 1.406 |
| legacy bound() | 1.275 |
| new plan.get | 1.150 |
| new bound() | 1.208 |

Results are sub-microsecond and noisy; the new path is ~8–15% faster than the legacy path but still ~8–20% behind Injex.

## Remaining overhead vs Injex

- **Prelude tuple allocation per resolve.** `pre()` folds the two singleton cells into a fresh `Tuple` on every resolution. Injex exec-inlines singletons into the resolver source, so it has no tuple allocation.
- **Dispatch guards on the hit path.** `ExecutionPlan.get` reads `container is not None`, `_override_layers`, `_tracer`, then does `resolvers[lookup]` inside a `try`. `BoundResolver.__call__` re-reads `plan.container` and the override/tracer guard each call. Injex does a single `self._noscope_creators[interface]` hit with no policy/override/tracer concept.
- **Closure indirection depth.** Ours is root → prelude → singleton wrappers (2–3 frames). Injex collapses the whole graph into one frame via `exec`.
- **EAFP `try/except` setup.** Every hot call pays the `try` setup cost; Injex relies on a guaranteed-hit dict index with no exception frame.
- **Wider dispatch dict.** Ours is a single `resolvers` dict keyed by `Key` including qualifiers; Injex uses a narrow `interface`-keyed creator dict. Identical lookup semantics, but our dict is broader and the lookup happens through a general key path.
- **No bounded single-frame collapse beyond the finite template set.** Without codegen, arbitrary-depth inlining degenerates back into nested calls; the added literal templates cover only the common arity-2 leaf shapes.

These are inherent to the no-`exec`/no-codegen constraint from issue #44.