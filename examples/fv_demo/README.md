# Formal-verification evidence source — demo

`MiniTokenBug.sol` has a conservation bug: `burn()` decrements a balance but forgets `totalSupply`,
so the aggregate desyncs from the sum of balances. `test/Conservation.t.sol :: check_invariant`
asserts the conservation law; Halmos returns a concrete counterexample.

## Through the `hound verify` command (the wired-in path)

```bash
# 1. register the project + build the graph (graph build uses your configured LLM)
hound project create fvdemo examples/fv_demo/src
hound graph build fvdemo --auto --files "MiniTokenBug.sol"

# 2. confirm the conservation property by SOUND counterexample. The graph builder names nodes
#    itself, so pass the id it produced for the burn function (list ids in graphs/graph_*.json).
hound verify fvdemo \
  --function check_invariant --workdir examples/fv_demo --contract Conservation \
  --node "<the func_* id for MiniTokenBug.burn from your built graph>"

# 3. the verified finding is now in the report like any other confirmed finding
hound report fvdemo --format markdown
```

`hound verify` resolves the finding to a **real** graph node, runs Halmos, and — only on a
counterexample — records a `status=confirmed`, `verified=True`, `verified_by=halmos` hypothesis. On a
passing check (or if Halmos/`uvx` is not installed) it records **nothing**: the source never
speculates, so it can only raise precision.

## Standalone (no project/graph, just the mechanism)

```bash
cd examples/fv_demo
forge build
python run_demo.py        # requires `portalocker` and `uvx` (for halmos) on PATH
```

`run_demo.py` runs the same check and shows the counterexample becoming a confirmed, verified
hypothesis in a throwaway `HypothesisStore`.
