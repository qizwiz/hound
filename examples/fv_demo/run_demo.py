#!/usr/bin/env python3
"""End-to-end demo: a REAL Halmos counterexample becomes a CONFIRMED, verified hypothesis in
hound's belief store -- the formal-verification evidence source in action.

Run:
    cd examples/fv_demo
    forge build
    python run_demo.py          # needs `portalocker` + `uvx` (halmos) on PATH

What it proves: the FV source only ever writes a SOUND finding (a concrete counterexample), and
that finding lands as status='confirmed', verified=True -- the one evidence tier hound's lifecycle
otherwise reserves for the finalize agent, because this one isn't fallible.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# make hound importable from the repo root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from analysis.concurrent_knowledge import HypothesisStore  # noqa: E402
from analysis.fv_evidence import HalmosFinding, record_finding, run_halmos  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> int:
    print("== building the demo contract (forge) ==")
    subprocess.run(["forge", "build"], cwd=HERE, check=True, capture_output=True)

    print("== running halmos on check_invariant (totalSupply conservation) ==")
    result = run_halmos(workdir=str(HERE), function="check_invariant", contract="Conservation")
    if result.status == "error":
        print(f"halmos did not produce a sound result: {result.detail}")
        return 1
    if result.status == "held":
        print("halmos PASSED -> nothing recorded. The FV source never speculates.")
        return 0
    print(f"halmos COUNTEREXAMPLE (sound witness):\n    {result.counterexample}\n")

    store_path = Path(tempfile.mkdtemp()) / "hypotheses.json"
    store = HypothesisStore(store_path, agent_id="halmos")

    print("== recording the sound counterexample into hound's belief store ==")
    hyp_id, status = record_finding(
        store,
        HalmosFinding(
            property="totalSupply == sum(balances) across mint/burn",
            function="check_invariant",
            node_ref="func_MiniTokenBug_burn",  # illustrative; the real id comes from the built graph
            severity="high",
            counterexample=result.counterexample,
            argv="(in examples/fv_demo) uvx halmos --function check_invariant --contract Conservation",
        ),
        confirm=True,
    )
    print(f"-> hypothesis {hyp_id}  status={status}\n")

    data = json.loads(store_path.read_text())
    h = data["hypotheses"][hyp_id]
    print("== resulting hypothesis (hound belief store) ==")
    print(json.dumps(h, indent=2))

    assert h["status"] == "confirmed" and h.get("verified") is True, "demo invariant failed!"
    print("\nOK: hound now holds a CONFIRMED, verified=True hypothesis derived from a sound "
          "halmos counterexample -- no LLM in the confirm path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
