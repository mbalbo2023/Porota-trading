#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${1:-/opt/porota-trading}"
TARGET="$ROOT/bg_paper_dashboard.py"
MODE="${2:---check}"

if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo "USAGE=$0 [repo_root] [--check|--apply]"
  exit 2
fi
if [[ ! -f "$TARGET" ]]; then
  echo "STATUS=BLOCKED"
  echo "REASON=DASHBOARD_FILE_MISSING"
  exit 0
fi

python3 - "$TARGET" "$MODE" <<'PY'
from pathlib import Path
import sys, shutil, datetime

path=Path(sys.argv[1])
mode=sys.argv[2]
text=path.read_text(encoding="utf-8")
original=text

required={
  "version_import":"from _version import VERSION",
  "document_theme":"f\"<title>{_e(title)}</title>{THEME}</head><body id='top'>",
  "home_tail":"+ _daily_summary_panel(data) + _balances_panel())",
  "reports_start":"def reports_page():",
  "reports_end":"\n\ndef sre_page(",
}
missing=[name for name,anchor in required.items() if anchor not in text]
if missing:
    print("STATUS=BLOCKED")
    print("REASON=MISSING_EXPECTED_ANCHORS")
    print("MISSING="+",".join(missing))
    raise SystemExit(0)

imports=(
"from df_daily_operation_summary_hf6 import summarize as daily_operation_summaries\n"
"from dg_dashboard_daily_result_ux_hf6 import daily_results_html, report_cards_html, RESPONSIVE_CSS\n"
"from dh_dashboard_compact_lists_hf6 import COMPACT_CSS\n"
)
if "from df_daily_operation_summary_hf6 import summarize as daily_operation_summaries" not in text:
    text=text.replace("from _version import VERSION\n",
                      "from _version import VERSION\n"+imports,1)

text=text.replace(
    "f\"<title>{_e(title)}</title>{THEME}</head><body id='top'>",
    "f\"<title>{_e(title)}</title>{THEME}{RESPONSIVE_CSS}{COMPACT_CSS}</head><body id='top'>",
    1,
)

helper='''\n\ndef _daily_results_panel():\n    try:\n        uri="file:"+str(Path(DB_PATH).resolve())+"?mode=ro"\n        with closing(sqlite3.connect(uri,uri=True,timeout=5)) as c:\n            c.row_factory=sqlite3.Row\n            days=daily_operation_summaries(c,limit_days=7)\n        return daily_results_html(days)\n    except (sqlite3.Error,ValueError,TypeError,ArithmeticError):\n        return ("<section class='paper-card'><h2>Resultado de las últimas jornadas</h2>"\n                "<div class='paper-warning'>Resumen diario no conciliable; no se inventa un resultado.</div></section>")\n'''
if "def _daily_results_panel():" not in text:
    marker="\ndef home_page():"
    if marker not in text:
        print("STATUS=BLOCKED")
        print("REASON=HOME_PAGE_ANCHOR_MISSING")
        raise SystemExit(0)
    text=text.replace(marker,helper+marker,1)

text=text.replace(
    "+ _daily_summary_panel(data) + _balances_panel())",
    "+ _daily_results_panel() + _daily_summary_panel(data) + _balances_panel())",
    1,
)

start=text.index("def reports_page():")
end=text.index("\n\ndef sre_page(",start)
new_reports='''def reports_page():\n    reports=_rows("SELECT * FROM report_registry ORDER BY period_key DESC,period_type") if _table("report_registry") else []\n    today=datetime.now(TZ).date().isoformat()\n    # IA intradía/paquetes IA: legado deprecado. No forman parte de la operación HF6-v2.\n    reports=[r for r in reports if r.get('period_type')!='IA_SEMANAL'\n             and (r.get('period_type')!='DIARIO' or r.get('period_key')==today)]\n    body=("<h1>Reportes</h1>"\n          "<p class='paper-muted'>Reportes operativos PAPER. La experiencia principal usa tarjetas verticales para tablet/móvil.</p>"\n          + report_cards_html(reports)\n          + "<div class='paper-notice'>Los diarios anteriores se consultan desde Aprendizaje. "\n            "Los artefactos IA heredados permanecen sólo por trazabilidad y no participan de HF6-v2.</div>")\n    return _document("Reportes",body,refresh=300)\n'''
text=text[:start]+new_reports+text[end:]

if text.count("def _daily_results_panel():") != 1:
    print("STATUS=BLOCKED")
    print("REASON=DAILY_RESULTS_HELPER_COUNT")
    raise SystemExit(0)
if text.count("def reports_page():") != 1:
    print("STATUS=BLOCKED")
    print("REASON=REPORTS_PAGE_COUNT")
    raise SystemExit(0)
if "Paquete IA semanal consolidado" in text[text.index("def reports_page():"):text.index("\n\ndef sre_page(")]:
    print("STATUS=BLOCKED")
    print("REASON=LEGACY_AI_REPORT_LINK_STILL_VISIBLE")
    raise SystemExit(0)
if "daily_results_html(days)" not in text or "report_cards_html(reports)" not in text:
    print("STATUS=BLOCKED")
    print("REASON=RESPONSIVE_RENDERER_NOT_WIRED")
    raise SystemExit(0)

print("STATUS=READY_TO_APPLY" if mode=="--check" else "STATUS=APPLYING")
print("TARGET="+str(path))
print("DAILY_RESULT_PANEL=READY")
print("REPORTS_RESPONSIVE_CARDS=READY")
print("LEGACY_AI_REPORT_MAIN_VIEW=HIDDEN")
print("HORIZONTAL_SCROLL_PRIMARY_REPORTS=REMOVED")

if mode=="--apply":
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup=path.with_name(path.name+".pre-hf6-v2-responsive-"+stamp)
    shutil.copy2(path,backup)
    path.write_text(text,encoding="utf-8")
    print("BACKUP="+str(backup))
    print("STATUS=APPLIED")
else:
    print("FILE_MUTATION=NO")
PY
true
