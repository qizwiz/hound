"""
Verify command: confirm a hypothesis with a SOUND formal-verification counterexample.

`hound verify` runs a user-provided Halmos (symbolic EVM) check against a real graph node and,
ONLY on a concrete counterexample for that function, records an auto-confirmed `verified` finding in
the project's hypothesis store. It is the formal-verification evidence source from the paper
(Section 2.5).

This is a second, explicitly-tagged confirm channel that sits alongside `finalize`:
  - finalize confirms LLM beliefs by judgement (the existing gate; unchanged here);
  - verify confirms by sound counterexample, via HypothesisStore.confirm_from_verifier.
Two confirm provenances, one status, distinguishable by `verified_by`.

It distinguishes "the property holds" from "the check never ran": a typo'd workdir, an unbuilt
contract, or a missing function is an ERROR (non-zero exit), never a silent pass.
"""

import json
import sys
from pathlib import Path

import click
from rich.console import Console

from analysis.concurrent_knowledge import HypothesisStore
from analysis.fv_evidence import HalmosFinding, record_finding, run_halmos
from commands.project import ProjectManager

console = Console()


def _graph_nodes(project_dir: Path) -> list[dict]:
    """Return all graph nodes across the project's graphs/graph_*.json files."""
    nodes: list[dict] = []
    for gf in sorted((project_dir / "graphs").glob("graph_*.json")):
        try:
            data = json.loads(gf.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        nodes.extend(data.get("nodes", []) or [])
    return nodes


def _validate_node_id(project_dir: Path, node: str) -> str:
    """Require that --node is a REAL id present in the project's graphs (never fabricate a binding)."""
    ids = {str(n.get("id")) for n in _graph_nodes(project_dir) if n.get("id")}
    if node in ids:
        return node
    func_ids = sorted(i for i in ids if i.startswith("func"))
    console.print(f"[red]Node '{node}' is not in this project's graphs.[/red]")
    if func_ids:
        console.print(f"[yellow]Function nodes available:[/yellow] {', '.join(func_ids[:25])}")
    sys.exit(1)


@click.command()
@click.argument("project_name")
@click.option("--function", "-f", required=True, help="The halmos check_* function to run (e.g. check_invariant)")
@click.option("--workdir", "-w", required=True, help="Foundry project dir containing the halmos test")
@click.option("--node", "-n", required=True, help="Graph node id to bind the finding to (validated against the graph)")
@click.option("--contract", "-c", default=None, help="Contract name to scope the halmos run (optional)")
@click.option("--property", "property_text", default=None, help="Human-readable property being checked")
@click.option("--severity", type=click.Choice(["low", "medium", "high", "critical"]), default="high",
              help="Severity if violated (default: high)")
@click.option("--timeout", default=240, type=int, help="Halmos timeout in seconds (default: 240)")
@click.option("--no-confirm", is_flag=True, help="Record as supporting evidence only; leave confirmation to finalize")
def verify(
    project_name: str,
    function: str,
    workdir: str,
    node: str,
    contract: str | None,
    property_text: str | None,
    severity: str,
    timeout: int,
    no_confirm: bool,
):
    """Confirm a finding with a sound Halmos counterexample (formal-verification evidence source)."""
    # Validate in the body too: hound.py's _invoke_click bypasses click's Choice parsing.
    if severity not in ("low", "medium", "high", "critical"):
        console.print(f"[red]Invalid --severity '{severity}' (choose low/medium/high/critical).[/red]")
        sys.exit(1)
    manager = ProjectManager()
    project = manager.get_project(project_name)
    if not project:
        console.print(f"[red]Project '{project_name}' not found.[/red]")
        sys.exit(1)

    project_dir = Path(project["path"])
    if not list((project_dir / "graphs").glob("graph_*.json")):
        console.print("[red]No graphs found. Run 'hound graph build' first.[/red]")
        sys.exit(1)

    node_id = _validate_node_id(project_dir, node)
    prop = property_text or f"{function} holds"

    console.print(f"[bold cyan]Verifying[/bold cyan] {prop} on node [green]{node_id}[/green] via halmos...")
    result = run_halmos(workdir=workdir, function=function, contract=contract, timeout=timeout)

    if result.status == "error":
        console.print(f"[red]Could not verify (no sound result): {result.detail}[/red]")
        sys.exit(1)
    if result.status == "held":
        console.print("[green]Property holds: halmos ran and found no counterexample. Nothing recorded.[/green]")
        sys.exit(0)

    console.print(f"[red]Counterexample found:[/red]\n{result.counterexample}")
    store = HypothesisStore(project_dir / "hypotheses.json", agent_id="halmos")
    replay = f"(in {workdir}) uvx halmos --function {function}" + (f" --contract {contract}" if contract else "")
    finding = HalmosFinding(
        property=prop,
        function=function,
        node_ref=node_id,
        severity=severity,
        counterexample=result.counterexample,
        argv=replay,
    )
    hyp_id, status = record_finding(store, finding, confirm=not no_confirm)

    if status == "error" or (not no_confirm and status != "confirmed"):
        console.print(f"[red]Failed to record the finding (store status: '{status}'). Nothing was persisted.[/red]")
        sys.exit(1)

    verb = "confirmed (verified by halmos)" if not no_confirm else f"recorded as supporting evidence (status: {status})"
    console.print(f"[bold green]Finding {hyp_id} {verb}.[/bold green]")
    sys.exit(0)
