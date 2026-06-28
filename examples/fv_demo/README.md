# Formal-verification evidence source — demo

This demo realizes the FV plug-in slot from the hound paper (§2.5): when **Halmos** (symbolic EVM
execution) finds a *counterexample*, it is recorded in hound's hypothesis store as a **verified,
confirmed** finding — the one evidence tier the belief lifecycle otherwise reserves for the finalize
agent, because a symbolic counterexample is *ground truth*, not an LLM confidence score.

## What it shows

`src/MiniTokenBug.sol` has a conservation bug: `burn()` decrements a balance but forgets
`totalSupply`, so the aggregate desyncs from the sum of balances. The halmos test
`test/Conservation.t.sol :: check_invariant` asserts the conservation law, and halmos returns a
concrete counterexample (a `(mint amount, burn amount)` pair that breaks it).

`run_demo.py` runs that check through `analysis/fv_evidence.py` and records the counterexample into a
fresh `HypothesisStore`, which then holds a hypothesis with:

```
status      = confirmed
verified    = true
verified_by = halmos
confidence  = 1.0
```

If the property *held* (no counterexample), the adapter writes **nothing**: the FV source never
speculates, so it can only ever raise precision, never lower it. The existing LLM lifecycle is
untouched — `add_evidence` / `adjust_confidence` still cannot confirm; only `confirm_from_verifier`
(sound evidence only) can.

## Run

```bash
cd examples/fv_demo
forge build
python run_demo.py        # requires `portalocker` and `uvx` (for halmos) on PATH
```
