# Speed comparison

doppy-di targets fast dependency resolution in **pure Python** with no runtime
code generation via `exec` and no compiled extensions. The compiled
`ExecutionPlan` pre-builds single-purpose resolver closures at compile time,
so the hot path is a plain dict lookup plus a call — no exception frame, no
per-call override or tracer guard, and the explicit `guardless` fast path even
strips singleton locks.

Speed is not free, and the fastest result needs the most stripped-down build.
The bare `ExecutionPlan.get` costs a little over hand-written code, and the
guardless mode trims further. Enabling the feature-rich set is inexpensive —
the gap stays small and constant, as the figures below show.

## Methodology

Timing protocol from `benchmarks/` (`compare_120.py`, `resolve_graph.py`):

- GC is disabled during measurement.
- Warmup, then a median over repeated samples (250k-400k iterations each).
- Sub-microsecond scale; numbers are noisy run to run — read them as relative,
  not absolute.

The six-object register-user graph is the headline comparison.

## Cross-library comparison (register-user graph)

Two shared singletons (`Settings`, `ApiClient`), four transients
(`UserRepository`, `EmailSender`, `AuditLog`, `RegisterUser`), root
`RegisterUser`. Median µs/op, GC off, from `resolve_graph.py`.

| implementation | median µs/op | vs manual |
|---|---|---|
| manual | 0.786 | 1.0x |
| injex | 0.908 | 1.16x |
| doppy-di frozen | 1.226 | 1.56x |
| doppy-di compiled | 1.311 | 1.67x |
| doppy-di guardless | 1.450 | 1.84x |
| dishka | 1.837 | 2.34x |
| wireup (same scope) | 2.157 | 2.74x |
| dependency-injector | 2.167 | 2.76x |
| wireup (scope/op) | 4.528 | 5.76x |
| doppy-di (uncompiled) | 15.638 | 19.9x |
| lagom | 27.662 | 35.2x |
| punq | 186.591 | 237x |

In pure Python and without `exec` codegen, doppy-di tracks the hand-written
floor and the `exec`-based injex within a small constant factor, while
`compile()` is ~12x faster than the feature-complete uncompiled container.

## doppy-di paths on the same graph

Dedicated comparison of the compiled `ExecutionPlan` routes (from
`benchmarks/compare_120.py`).

| implementation | median µs/op | vs manual |
|---|---|---|
| manual | 0.79 | 1.0x |
| legacy plan.get | 1.63 | 2.06x |
| legacy bound() | 1.25 | 1.58x |
| compiled plan.get | 1.31 | 1.66x |
| compiled bound() | 1.14 | 1.44x |
| frozen plan.get | 1.06 | 1.34x |
| frozen bound() | 1.03 | 1.30x |
| **guardless plan.get** | **1.04** | **1.32x** |
| **guardless bound()** | **1.09** | **1.38x** |

The guardless fast path is doppy-di's fastest: one direct dict lookup per
root, no per-call override, tracer, or EAFP exception-frame guard. `bound()`
re-derives the root key once at bind time and then calls the stored closure
directly.

## Shared-singleton, two-leaf root

Root transient over two transient leaves, each pulling two singletons.
Exercises the `literal2` flat resolver templates.

| implementation | median µs/op | vs manual |
|---|---|---|
| manual | 0.73 | — |
| legacy plan.get | 1.42 | 1.94x |
| frozen plan.get | 1.00 | 1.36x |
| **guardless plan.get** | **0.98** | **1.33x** |
| **guardless bound()** | **0.95** | **1.29x** |

## Notes on flattery

- Sub-microsecond medians swing run to run. Favor the relative ordering over
  any single absolute value.
- A singleton-only chain (no transient allocation) is not a reliable
  reference: caching libs return cached instances, so its numbers fluctuate
  and are excluded from this page.
- `benchmarks/statistical_comparison.py` runs many interleaved rounds and
  tests the differences statistically: among doppy-di's compiled paths
  (compiled / frozen / guardless) none differs significantly from another at
  alpha=0.05, and guardless vs frozen is a statistical tie (p=0.59). The
  ordering above reflects a single dedicated run, not a guaranteed gap.

## Cold-start (build + register + validate + first resolve)

| implementation | median ms |
|---|---|
| doppy-di | 0.081 |
| injex | 0.154 |
| dependency-injector | 0.211 |
| doppy-di guardless | 0.365 |
| lagom | 0.425 |
| punq | 0.437 |
| doppy-di compiled | 0.454 |
| dishka | 3.312 |
| wireup (same scope) | 4.253 |

The guardless and compiled plans add a one-time compile cost; the uncompiled
plain container still starts fastest.