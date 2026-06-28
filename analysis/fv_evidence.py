"""
fv_evidence.py -- a formal-verification evidence source for hound.

Hound's belief lifecycle treats every evidence source as fallible: the analysis agent can
*support* or *refute* a hypothesis but cannot *confirm* it; only the finalize agent confirms
(see HypothesisStore.adjust_confidence). That is exactly right for LLM evidence, which is a
confidence, not a fact.

A formal verifier is the exception. When Halmos (symbolic EVM execution) returns a
*counterexample for the requested function*, it is a concrete input that provably violates a
property -- ground truth, not a guess. This module runs a Halmos check over a function and, ON A
COUNTEREXAMPLE FOR THAT FUNCTION, records it in the HypothesisStore as a *verified* finding allowed
to confirm (via HypothesisStore.confirm_from_verifier).

Soundness invariants (these are the whole point):
  - It distinguishes three outcomes -- VIOLATED / HELD / ERROR -- and never reports "held" when the
    check did not actually run (a typo'd workdir, an unbuilt contract, or a missing function are
    ERROR, not a silent pass).
  - It binds a counterexample to the EXACT function requested (halmos --function is a prefix match,
    so it reads the [FAIL] <function> line, not just the first counterexample block).
  - It only ever records a sound finding, and reports success only if the store actually confirmed.

This is the FV plug-in slot envisioned in the hound paper (Section 2.5), as an additive evidence
source. The existing LLM lifecycle is untouched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from analysis.concurrent_knowledge import Evidence, Hypothesis, HypothesisStore

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CEX = re.compile(r"Counterexample:\s*\n?(.*?)(?:\n\s*\n|\n\s*\[|\n\s*Symbolic test result|\Z)", re.S)
# Known limitation: this captures only bare `name = 0x.. / decimal` model lines. Exotic halmos
# renderings (empty bytes `0x`, selector-name values) are dropped, so the witness text may degrade to
# "(not parsed)". This is a fidelity loss only -- the VIOLATED verdict is driven by the [FAIL] line,
# never by witness parsing, so a real violation is still recorded.
_WITNESS = re.compile(r"^\s*\S+\s*=\s*(?:0x[0-9a-fA-F]+|\d+)\s*$")
# per-function result boundaries, used to scope a counterexample to its own function's block
_BOUNDARY = re.compile(r"\[(?:FAIL|PASS)\]|Running \d+ test|Symbolic test result")


@dataclass
class HalmosFinding:
    """A sound property violation found by Halmos."""
    property: str        # human-readable invariant that was violated
    function: str        # the check_* test function
    node_ref: str        # the hound graph node id for the function under test
    severity: str        # low / medium / high / critical
    counterexample: str  # the concrete witness lines, verbatim from Halmos
    argv: str            # the exact command, for replay


@dataclass
class HalmosResult:
    """Outcome of running one Halmos check, with the three outcomes kept distinct."""
    status: str                       # "violated" | "held" | "error"
    counterexample: str | None = None  # set only when status == "violated"
    detail: str = ""                  # human-readable reason for "error"/"held"


def _extract_counterexample(out: str, function: str) -> str | None:
    """Return the witness for THIS function's counterexample, or None.

    halmos --function is a prefix match, so the output may contain several functions' results. We
    locate the `[FAIL] <function>` line and take the counterexample block immediately preceding it,
    so a violation is never mis-attributed to a different function.
    """
    fail = re.search(rf"\[FAIL\]\s+{re.escape(function)}\b", out)
    if not fail:
        return None
    head = out[: fail.start()]
    # scope to THIS function's block only: start after the previous result boundary, so a different
    # function's counterexample (e.g. a sibling that FAILed earlier) is never attributed here.
    start = 0
    for b in _BOUNDARY.finditer(head):
        start = b.end()
    block = None
    for m in _CEX.finditer(head[start:]):
        block = m
    if not block:
        return None
    witness = [ln.strip() for ln in block.group(1).splitlines() if _WITNESS.match(ln)]
    return "\n".join(witness) if witness else None


def run_halmos(workdir: str, function: str, contract: str | None = None,
               timeout: int = 240) -> HalmosResult:
    """Run one Halmos check and classify the outcome as violated / held / error.

    error  = the check did not actually run (no tool, crash, or the requested function never
             executed -- e.g. bad workdir, unbuilt contract, wrong name). NOT a pass.
    held   = the requested function ran and passed (no counterexample).
    violated = the requested function produced a concrete counterexample (sound).
    """
    if shutil.which("uvx") is None:
        return HalmosResult("error", detail="`uvx` (halmos) not found on PATH")
    argv = ["uvx", "halmos", "--function", function]
    if contract:
        argv += ["--contract", contract]
    try:
        r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # tool failure / timeout: not a pass
        return HalmosResult("error", detail=f"halmos failed to run: {e}")

    out = _ANSI.sub("", (r.stdout or "") + (r.stderr or ""))
    ran_fail = re.search(rf"\[FAIL\]\s+{re.escape(function)}\b", out)
    ran_pass = re.search(rf"\[PASS\]\s+{re.escape(function)}\b", out)

    if ran_fail:
        cex = _extract_counterexample(out, function)
        if cex:
            return HalmosResult("violated", counterexample=cex)
        # halmos reported FAIL for our function but we could not parse a model -- still a violation,
        # but be honest the witness is unparsed rather than silently dropping it.
        return HalmosResult("violated", counterexample="(halmos reported a violation; witness not parsed)")
    if ran_pass:
        return HalmosResult("held")
    return HalmosResult(
        "error",
        detail=(f"halmos did not execute '{function}' (check --workdir, run `forge build`, and the "
                f"--function/--contract names). Exit code {r.returncode}."),
    )


def _existing_id_by_title(store: HypothesisStore, title: str, node_ref: str) -> str | None:
    """Recover the id of the existing hypothesis with this exact title AND node. propose() dedups by
    title only; requiring the node to match too means a verifier can never stamp an unrelated record
    that merely happens to share a title."""
    low = title.lower()
    for h in store.list_all():
        if (h.get("title") or "").lower() == low and node_ref in (h.get("node_refs") or []):
            return h.get("id")
    return None


def _status_of(store: HypothesisStore, hyp_id: str) -> str:
    for h in store.list_all():
        if h.get("id") == hyp_id:
            return str(h.get("status", "unknown"))
    return "absent"


def record_finding(store: HypothesisStore, finding: HalmosFinding, *, confirm: bool = True) -> tuple[str, str]:
    """Write a verified Halmos finding into hound's belief store. Returns (hypothesis_id, status).

    The node id is in the title, so distinct nodes never collide; a re-run on the same node dedups to
    the same hypothesis. The returned status reflects what the store ACTUALLY did (it never claims
    'confirmed' when the underlying write was a no-op), so the caller can trust it.
    """
    hyp = Hypothesis(
        title=f"[halmos] {finding.property} violated in {finding.function} ({finding.node_ref})",
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
    if ok:
        hyp_id = ret
    else:
        # propose() rejected -- recover the EXISTING hypothesis id (it dedups by title), so we
        # augment/confirm the real record rather than a non-existent content hash.
        hyp_id = _existing_id_by_title(store, hyp.title, finding.node_ref)
        if hyp_id is None:
            return "", "error"

    evidence = Evidence(
        description=f"halmos counterexample (sound): {finding.counterexample}",
        type="supports",
        confidence=1.0,
        node_refs=[finding.node_ref],
        created_by="halmos",
    )
    if confirm:
        if store.confirm_from_verifier(hyp_id, evidence, verifier_name="halmos"):
            return hyp_id, "confirmed"
        return hyp_id, "error"
    # --no-confirm path: add_evidence has no dedup, so keep this idempotent too (one verifier witness
    # per hypothesis), since symbolic witness text is non-deterministic across runs.
    existing = next((h for h in store.list_all() if h.get("id") == hyp_id), None)
    if existing and any(e.get("created_by") == "halmos" and e.get("type") == evidence.type
                        for e in (existing.get("evidence") or [])):
        return hyp_id, _status_of(store, hyp_id)
    if not store.add_evidence(hyp_id, evidence):
        return hyp_id, "error"
    return hyp_id, _status_of(store, hyp_id)
