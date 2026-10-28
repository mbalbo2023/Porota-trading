"""A committed SHADOW cut must disclose bounded observation coverage."""
from types import SimpleNamespace
from unittest import TestCase

from rc6_trader_dashboard.views_system import render


class CoverageViewTests(TestCase):
    def test_partial_observations_are_visible_in_evidence_popup(self):
        for flag, expected in ((True, "COBERTURA_PARCIAL"),
                               (False, "SCOPE_COMPLETO"),
                               (None, "NO_VERIFICADO")):
            with self.subTest(flag=flag):
                quality = {} if flag is None else {"observation_read_truncated": flag}
                projection = SimpleNamespace(shadow={
                    "state": "COMMITTED_COHERENT_SHADOW", "reason": "CURRENT",
                    "pointer": {}, "manifest": {}, "report": {"input_quality": quality}})
                html = render(projection, None, "evidencia")
                self.assertIn("Cobertura de observaciones SHADOW", html)
                self.assertIn(expected, html)
                self.assertIn("<dialog", html)
