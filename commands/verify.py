"""
Verify command: confirm a hypothesis with a SOUND formal-verification counterexample.

`hound verify` runs a user-provided Halmos (symbolic EVM) check against a real graph node and,
ONLY on a concrete counterexample, records an auto-confirmed `verified` finding in the project's
hypothesis store. It is the formal-verification evidence source from the paper (Section 2.5).

This is a second, explicitly-tagged confirm channel that sits alongside `finalize`:
  - finalize confirms LLM beliefs by judgement (the existing gate; unchanged here);
  - verify confirms by sound counterexample, via HypothesisStore.confirm_from_verifier
    (status='confirmed', verified_by='halmos').
Two confirm provenances, one status, distinguishable by `verified_by`.

On a passing check (or if Halmos/uvx is not installed) it records NOTHING -- the source never
speculates, so it can only ever raise precision.
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


def _graph_node_ids(project_dir: Path) -> list[dict]:
    """Return all graph nodes across the project's graphs/graph_*.json files."""
    nodes: list[dict] = []
    graphs_dir = project_dir / "graphs"
    for gf in sorted(graphs_dir.glob("graph_*.json")):
        try:
            data = json.loads(gf.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        nodes.extend(data.get("nodes", []) or [])
    return nodes


def _validate_node_id(project_dir: Path, node: str) -> str:
    """Require that --node is a REAL id present in the project's graphs.

    Never fabricates a binding: if the id is not in any graphs/graph_*.json, error (and list the
    available function nodes) rather than recording a finding that dangles off a non-existent node.
    """
    ids = {str(n.get("id")) for n in _graph_node_ids(project_dir) if n.get("id")}
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
@click.option("--severity", default="high", help="Severity if violated: low/medium/high/critical (default: high)")
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
    manager = ProjectManager()
    project = manager.get_project(project_name)
    if not project:
        console.print(f"[red]Project '{project_name}' not found.[/red]")
        sys.exit(1)

    project_dir = Path(project["path"])
    graphs_dir = project_dir / "graphs"
    if not graphs_dir.exists() or not list(graphs_dir.glob("*.json")):
        console.print("[red]No graphs found. Run 'hound graph build' first.[/red]")
        sys.exit(1)

    node_id = _validate_node_id(project_dir, node)
    prop = property_text or f"{function} holds"

    console.print(f"[bold cyan]Verifying[/bold cyan] {prop} on node [green]{node_id}[/green] via halmos...")
    cex = run_halmos(workdir=workdir, function=function, contract=contract, timeout=timeout)
    if cex is None:
        console.print("[green]No counterexample (or halmos unavailable) -- nothing recorded.[/green]")
        sys.exit(0)

    console.print(f"[red]Counterexample found:[/red]\n{cex}")
    store = HypothesisStore(project_dir / "hypotheses.json", agent_id="verify")
    argv = f"halmos --function {function}" + (f" --contract {contract}" if contract else "")
    finding = HalmosFinding(
        property=prop,
        function=function,
        node_ref=node_id,
        severity=severity,
        counterexample=cex,
        argv=argv,
    )
    hyp_id, status = record_finding(store, finding, confirm=not no_confirm)

    if not no_confirm and status != "confirmed":
        console.print(f"[yellow]Warning:[/yellow] recorded {hyp_id} but status is '{status}', not confirmed "
                      "(possible title/node collision with an existing hypothesis).")
        sys.exit(0)

    verb = "confirmed (verified by halmos)" if not no_confirm else "recorded as supporting evidence"
    console.print(f"[bold green]Finding {hyp_id} {verb}.[/bold green]")
    sys.exit(0)
