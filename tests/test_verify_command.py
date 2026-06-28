"""Tests for the `hound verify` command (formal-verification evidence source).

These never invoke real halmos: `commands.verify.run_halmos` is patched, so the suite passes on
any machine (including CI) without foundry/halmos installed.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from analysis.fv_evidence import HalmosResult
from commands.verify import verify

_CEX = "p_a_uint256_00 = 0x80000000000000000000000000000000\np_b_uint256_00 = 0x01"
VIOLATED = HalmosResult("violated", counterexample=_CEX)


class TestVerifyCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "graphs").mkdir()
        (self.tmp / "graphs" / "graph_Test.json").write_text(json.dumps({
            "nodes": [
                {"id": "func_MiniToken_burn", "type": "function", "label": "MiniToken.burn"},
                {"id": "func_MiniToken_mint", "type": "function", "label": "MiniToken.mint"},
            ],
            "edges": [],
        }))
        self.runner = CliRunner()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hyps(self) -> dict:
        f = self.tmp / "hypotheses.json"
        return json.loads(f.read_text()).get("hypotheses", {}) if f.exists() else {}

    def _run(self, node="func_MiniToken_burn", extra=None):
        args = ["proj", "-f", "check_invariant", "-w", "x", "-n", node] + (extra or [])
        return self.runner.invoke(verify, args)

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_violation_confirms_as_verified(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self._run()
        self.assertEqual(res.exit_code, 0, res.output)
        h = next(iter(self._hyps().values()))
        self.assertEqual(h["status"], "confirmed")
        self.assertTrue(h["verified"])
        self.assertEqual(h["verified_by"], "halmos")
        self.assertEqual(h["node_refs"], ["func_MiniToken_burn"])

    @patch("commands.verify.run_halmos", return_value=HalmosResult("held"))
    @patch("commands.verify.ProjectManager")
    def test_held_records_nothing(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self._run()
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(self._hyps(), {})

    @patch("commands.verify.run_halmos", return_value=HalmosResult("error", detail="did not run"))
    @patch("commands.verify.ProjectManager")
    def test_never_ran_is_error_not_pass(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self._run()
        self.assertEqual(res.exit_code, 1)  # 'never ran' must NOT look like a pass
        self.assertEqual(self._hyps(), {})

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_unknown_node_errors_without_running_halmos(self, mock_pm, mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self._run(node="func_nope")
        self.assertNotEqual(res.exit_code, 0)
        self.assertEqual(self._hyps(), {})
        mock_halmos.assert_not_called()

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_reverify_is_idempotent(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        self.assertEqual(self._run().exit_code, 0)
        self.assertEqual(self._run().exit_code, 0)  # re-verify still reports confirmed, not error
        hyps = self._hyps()
        self.assertEqual(len(hyps), 1)
        halmos_ev = [e for e in next(iter(hyps.values()))["evidence"] if e.get("created_by") == "halmos"]
        self.assertEqual(len(halmos_ev), 1)  # the witness is not duplicated on re-verify

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_distinct_nodes_do_not_collide(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        self.assertEqual(self._run(node="func_MiniToken_burn").exit_code, 0)
        self.assertEqual(self._run(node="func_MiniToken_mint").exit_code, 0)
        self.assertEqual(len(self._hyps()), 2)  # same property/function, different node -> two findings

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_bad_severity_is_rejected(self, mock_pm, mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self._run(extra=["--severity", "BOGUS"])
        self.assertNotEqual(res.exit_code, 0)
        self.assertEqual(self._hyps(), {})

    @patch("commands.verify.run_halmos", return_value=VIOLATED)
    @patch("commands.verify.ProjectManager")
    def test_no_confirm_reverify_is_idempotent(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        self.assertEqual(self._run(extra=["--no-confirm"]).exit_code, 0)
        self.assertEqual(self._run(extra=["--no-confirm"]).exit_code, 0)
        hyps = self._hyps()
        self.assertEqual(len(hyps), 1)
        halmos_ev = [e for e in next(iter(hyps.values()))["evidence"] if e.get("created_by") == "halmos"]
        self.assertEqual(len(halmos_ev), 1)


if __name__ == "__main__":
    unittest.main()
