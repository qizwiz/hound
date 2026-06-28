"""
fv_evidence.py -- a formal-verification evidence source for hound.

Hound's belief lifecycle treats every evidence source as fallible: the analysis agent can
*support* or *refute* a hypothesis but cannot *confirm* it; only the finalize agent confirms
(see HypothesisStore.adjust_confidence). That is exactly right for LLM evidence, which is a
confidence, not a fact.

A formal verifier is the exception. When Halmos (symbolic EVM execution) returns a
*counterexample*, it is a concrete input that provably violates a property -- ground truth, not a
guess. This module runs a Halmos check over a function and, ON A COUNTEREXAMPLE, records it in the
HypothesisStore as a *verified* finding allowed to confirm (via the new
HypothesisStore.confirm_from_verifier path). If Halmos finds no counterexample it writes nothing:
the FV source never speculates, so it can only ever raise precision, never lower it.

This is the FV plug-in slot envisioned in the hound paper (Section 2.5), realized as a ~1-file,
additive evidence source. The existing LLM lifecycle is untouched.

Usage:
    from analysis.concurrent_knowledge import HypothesisStore
    from analysis.fv_evidence import run_halmos, HalmosFinding, record_finding

    cex = run_halmos(workdir="examples/fv_demo", function="check_invariant")
    if cex:
        record_finding(store, HalmosFinding(
            property="totalSupply conserved across mint/burn",
            function="check_invariant", node_ref="func_MiniTokenBug.burn",
            severity="high", counterexample=cex,
            argv="halmos --function check_invariant"))
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from analysis.concurrent_knowledge import Evidence, Hypothesis, HypothesisStore

# A real Halmos counterexample block: "Counterexample:\n    p_a_... = 0x..\n    ..."
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CEX = re.compile(r"Counterexample:\s*\n?(.*?)(?:\n\s*\n|\n\s*\[|\n\s*Symbolic test result|\Z)", re.S)
_WITNESS = re.compile(r"^\s*\S+\s*=\s*(?:0x[0-9a-fA-F]+|\d+)\s*$")


@dataclass
class HalmosFinding:
    """A sound property violation found by Halmos."""
    property: str        # human-readable invariant that was violated
    function: str        # the check_* test function
    node_ref: str        # the hound graph node id for the function under test
    severity: str        # low / medium / high / critical
    counterexample: str  # the concrete witness lines, verbatim from Halmos
    argv: str            # the exact command, for replay


def run_halmos(workdir: str, function: str, contract: str | None = None,
               timeout: int = 240) -> str | None:
    """Run one Halmos check; return the verbatim counterexample block, or None if it PASSED.

    None means 'no sound violation' -- callers then write nothing. A non-None return is a concrete
    witness, not a confidence.
    """
    argv = ["uvx", "halmos", "--function", function]
    if contract:
        argv += ["--contract", contract]
    try:
        r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001 -- tool failure is not a finding
        return None
    out = _ANSI.sub("", (r.stdout or "") + (r.stderr or ""))
    m = _CEX.search(out)
    if not m:
        return None
    # keep only the model-assignment lines (name = 0x.. / name = 123); drop tool/lint noise
    witness = [ln.strip() for ln in m.group(1).splitlines() if _WITNESS.match(ln)]
    return "\n".join(witness) if witness else None


def record_finding(store: HypothesisStore, finding: HalmosFinding, *, confirm: bool = True) -> tuple[str, str]:
    """Write a verified Halmos finding into hound's belief store. Returns (hypothesis_id, status).

    confirm=True (default): a sound counterexample sets status='confirmed' via the verified path.
    confirm=False: it is recorded as strong supporting evidence and left for the finalize agent,
    preserving hound's default lifecycle for operators who want a human/finalize gate on top.
    """
    hyp = Hypothesis(
        title=f"[halmos] {finding.property} violated in {finding.function}",
        description=("Symbolic execution found a concrete input that violates the property "
                     f"`{finding.property}`. This is a sound counterexample, not an LLM conjecture."),
        vulnerability_type="invariant_violation",
        severity=finding.severity,
        confidence=1.0,
        node_refs=[finding.node_ref],
        reasoning=f"halmos counterexample:\n{finding.counterexample}\n\nreplay: {finding.argv}",
        properties={"verifier": "halmos", "verified": True, "replay": finding.argv},
        created_by="halmos",
    )
    ok, ret = store.propose(hyp)
    hyp_id = ret if ok else hyp.id  # on duplicate-title, the stable id is the same content hash

    evidence = Evidence(
        description=f"halmos counterexample (sound): {finding.counterexample}",
        type="supports",
        confidence=1.0,
        node_refs=[finding.node_ref],
        created_by="halmos",
    )
    if confirm:
        store.confirm_from_verifier(hyp_id, evidence, verifier_name="halmos")
        return hyp_id, "confirmed"
    store.add_evidence(hyp_id, evidence)
    return hyp_id, "investigating"
