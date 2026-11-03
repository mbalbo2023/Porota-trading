"""A635 documentation safety guard: RC6 README cannot regress to real mode.

No broker, host, container, credential, network or workflow operation.
"""
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_README_SHA = "0f1e14735a6fc3423e86b6bc091c58f65537d9f3"
SAFETY_MARKERS = (
    "# POROTA TRADING RC6",
    "PAPER/SHADOW ONLY",
    "PRODUCTION_PAPER / SIMULATION",
    "real_orders_sent=0",
    "Rutas de órdenes reales bloqueadas",
    "Ninguna opción, incluida Telegram o configuración, está autorizada a habilitar órdenes reales",
    "PPI Watch NO TOCAR",
    "FIX-FORWARD ONLY",
    "GitHub Actions",
    "Deploy V2",
    "build once",
    "manifest",
    "SHA",
    "NO_VERIFICADO",
    "READY",
    "COLD2",
    "#732",
    "#909",
)


def violations(document: str) -> list[str]:
    """Detect release-safety regressions, not infer the actual runtime."""
    problems = [f"README_MISSING_{marker}" for marker in SAFETY_MARKERS
                if marker not in document]
    forbidden = {
        "LEGACY_HEADER": r"(?im)^#\s*Porota Trading v16\.2\s*$",
        "MANUAL_DOCKER_COMPOSE": r"(?i)\bdocker\s+compose\b",
        "LEGACY_REAL_CHOICE": r"(?i)simulaci[oó]n\s+contra\s+el\s+sandbox\s+de\s+PPI\s*,\s*real\s*,\s*o",
        "LEGACY_TELEGRAM_REAL_CHOICE": r"(?i)Telegram[^\n]{0,100}(?:modo|opci[oó]n)\s+real\b",
        "UNPROVEN_CODE_ABSENCE": r"(?i)\bNo\s+existe\s+opci[oó]n\s+de\s+habilitar\s+trading\s+real\b",
        "COMMAND_BLOCK": r"(?im)^```(?:bash|shell|sh|console)\s*$",
        "COMMAND_LINE": r"(?im)^\s*(?:\$\s*)?(?:sudo\s+|ssh\s+|docker\s+(?:run|build|compose|exec))",
    }
    problems.extend(f"README_FORBIDDEN_{name}" for name, pattern in forbidden.items()
                    if re.search(pattern, document))
    if f"/blob/{LEGACY_README_SHA}/README.md" not in document:
        problems.append("README_LEGACY_PERMALINK_MISSING")
    if "no equivale a `DESPLEGADO` ni `VALIDADO_RUNTIME`" not in document:
        problems.append("README_CODE_RUNTIME_BOUNDARY_MISSING")
    if "Exact artifact predeploy gate=SKIPPED" not in document:
        problems.append("README_FOCUSED_CI_PREDEPLOY_BOUNDARY_MISSING")
    if "no es una etapa del embudo comercial" not in document:
        problems.append("README_COLD2_SCOPE_MISSING")
    return problems


class ReadmeSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def test_candidate_readme_is_rc6_paper_only(self):
        self.assertEqual(violations(self.document), [])

    def test_old_real_choice_would_fail_closed(self):
        old = "Al levantar, elegí simulación contra el sandbox de PPI, real, o no operar."
        self.assertIn("README_FORBIDDEN_LEGACY_REAL_CHOICE",
                      violations(self.document + "\n" + old))

    def test_old_header_would_fail_closed(self):
        old = re.sub(r"^# POROTA TRADING RC6.*$", "# Porota Trading v16.2",
                     self.document, count=1, flags=re.MULTILINE)
        self.assertIn("README_FORBIDDEN_LEGACY_HEADER", violations(old))

    def test_manual_compose_build_would_fail_closed(self):
        injected = self.document + "\n```text\ndocker compose build\n```"
        self.assertIn("README_FORBIDDEN_MANUAL_DOCKER_COMPOSE",
                      violations(injected))

    def test_shell_command_block_would_fail_closed(self):
        injected = self.document + "\n```bash\nssh root@host\n```"
        problems = violations(injected)
        self.assertIn("README_FORBIDDEN_COMMAND_BLOCK", problems)
        self.assertIn("README_FORBIDDEN_COMMAND_LINE", problems)

    def test_absent_paper_order_zero_would_fail_closed(self):
        injected = self.document.replace("real_orders_sent=0", "unchecked_orders")
        self.assertIn("README_MISSING_real_orders_sent=0", violations(injected))

    def test_focused_ci_is_not_full_predeploy(self):
        injected = self.document.replace("Exact artifact predeploy gate=SKIPPED",
                                         "Predeploy was green")
        self.assertIn("README_FOCUSED_CI_PREDEPLOY_BOUNDARY_MISSING",
                      violations(injected))

    def test_cold2_not_promoted_to_funnel_state(self):
        injected = self.document.replace("no es una etapa del embudo comercial",
                                         "es etapa del embudo comercial")
        self.assertIn("README_COLD2_SCOPE_MISSING", violations(injected))

    def test_prior_readme_remains_linked_but_not_operational(self):
        self.assertIn(f"/blob/{LEGACY_README_SHA}/README.md", self.document)
        self.assertIn("archivo histórico no operativo", self.document)
        self.assertNotIn("# Porota Trading v16.2", self.document)

    def test_policy_does_not_infer_historical_code_absence(self):
        injected = self.document + "\nNo existe opción de habilitar trading real."
        self.assertIn("README_FORBIDDEN_UNPROVEN_CODE_ABSENCE",
                      violations(injected))


if __name__ == "__main__":
    unittest.main()
