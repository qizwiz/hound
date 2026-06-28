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

from commands.verify import verify

_CEX = "p_a_uint256_00 = 0x80000000000000000000000000000000\np_b_uint256_00 = 0x01"


class TestVerifyCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "graphs").mkdir()
        (self.tmp / "graphs" / "graph_Test.json").write_text(json.dumps({
            "nodes": [{"id": "func_MiniToken_burn", "type": "function", "label": "MiniToken.burn"}],
            "edges": [],
        }))
        self.runner = CliRunner()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hyps(self) -> dict:
        f = self.tmp / "hypotheses.json"
        return json.loads(f.read_text()).get("hypotheses", {}) if f.exists() else {}

    @patch("commands.verify.run_halmos", return_value=_CEX)
    @patch("commands.verify.ProjectManager")
    def test_counterexample_confirms_as_verified(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self.runner.invoke(verify, [
            "proj", "--function", "check_invariant", "--workdir", "x", "--node", "func_MiniToken_burn",
        ])
        self.assertEqual(res.exit_code, 0, res.output)
        hyps = self._hyps()
        self.assertEqual(len(hyps), 1)
        h = next(iter(hyps.values()))
        self.assertEqual(h["status"], "confirmed")
        self.assertTrue(h["verified"])
        self.assertEqual(h["verified_by"], "halmos")
        self.assertEqual(h["node_refs"], ["func_MiniToken_burn"])

    @patch("commands.verify.run_halmos", return_value=None)
    @patch("commands.verify.ProjectManager")
    def test_no_counterexample_writes_nothing(self, mock_pm, _mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self.runner.invoke(verify, [
            "proj", "--function", "check_invariant", "--workdir", "x", "--node", "func_MiniToken_burn",
        ])
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(self._hyps(), {})

    @patch("commands.verify.run_halmos", return_value=_CEX)
    @patch("commands.verify.ProjectManager")
    def test_unknown_node_errors_without_running_halmos(self, mock_pm, mock_halmos):
        mock_pm.return_value.get_project.return_value = {"path": str(self.tmp)}
        res = self.runner.invoke(verify, [
            "proj", "--function", "check_invariant", "--workdir", "x", "--node", "func_nope",
        ])
        self.assertNotEqual(res.exit_code, 0)
        self.assertEqual(self._hyps(), {})
        mock_halmos.assert_not_called()


if __name__ == "__main__":
    unittest.main()
