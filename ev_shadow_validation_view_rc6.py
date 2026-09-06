"""HTML fragment for SHADOW→BINDING learning lanes in `/validacion`."""
from __future__ import annotations

import bg_paper_dashboard as bg
import et_shadow_learning_rc6 as learning

LABELS={
 'ECONOMIC_GATE':'Portón económico',
 'EXPECTANCY':'Expectancy',
 'MARKET_REGIME':'Régimen de mercado',
 'SECTOR_CONCENTRATION':'Concentración sectorial',
}


def _n(v):
    return bg._e('—' if v is None else v)


def render():
    data=learning.collect()
    cards=[]
    for key,label in LABELS.items():
        p=data.get('policies',{}).get(key,{})
        m=p.get('metrics',{})
        stage=p.get('stage','COLLECTING_EVIDENCE')
        sessions=p.get('sessions',0)
        precision=m.get('block_precision')
        precision='s/d' if precision is None else f"{precision*100:.1f}%"
        cards.append(
          "<details class='paper-trade'>"
          f"<summary>{bg._e(label)} · {bg._e(stage)} · {sessions}/~20 ruedas</summary>"
          "<div class='trade-body'>"
          f"<p><b>Autoridad actual:</b> SHADOW — <b>NO bloquea PAPER</b>. Promoción automática: NO.</p>"
          f"<p><b>Inicio observado:</b> {_n(p.get('first_observed'))} · <b>Ruedas con evidencia:</b> {sessions}</p>"
          "<div class='paper-grid'>"
          + bg._card('Evaluaciones',m.get('evaluated',0),'Veredictos contrafactuales','gray')
          + bg._card('Would allow',m.get('would_allow',0),'SHADOW habría permitido','green')
          + bg._card('Would block',m.get('would_block',0),'SHADOW habría bloqueado; PAPER continúa','yellow')
          + bg._card('Pérdidas evitables',m.get('losses_avoided',0),'Would-block que luego terminó en pérdida','green')
          + bg._card('Ganancias eliminadas',m.get('gains_removed',0),'False positive: would-block de una ganadora','red' if m.get('gains_removed',0) else 'gray')
          + bg._card('Pérdidas permitidas',m.get('losses_allowed',0),'False negative: would-allow perdedor','yellow' if m.get('losses_allowed',0) else 'gray')
          + bg._card('Outcome pendiente',m.get('outcome_pending',0),'Aún no hay cierre PAPER comparable','gray')
          + bg._card('Precisión del bloqueo',precision,'Sólo sobre would-block con resultado realizado','gray')
          + "</div>"
          f"<p><b>PnL PAPER observado:</b> {_n(m.get('actual_paper_pnl'))} · "
          f"<b>PnL contrafactual si este gate hubiese sido BINDING:</b> {_n(m.get('counterfactual_pnl_if_gate_bound'))}</p>"
          "<p><b>Siguiente hito:</b> acumular evidencia independiente y revisar false positives/negatives por familia, instrumento y régimen. "
          "El objetivo ~20 ruedas/4 semanas NO es un gatillo temporal.</p>"
          "</div></details>"
        )
    state=data.get('state','GRAY')
    notice=(
      "<div class='paper-warning'><b>Filosofía de esta campaña:</b> preferimos perder dinero ficticio y aprender ahora. "
      "Los learning gates observan qué habrían hecho, pero no vetan PAPER. Los hard safety blocks siguen obligatorios y separados.</div>"
    )
    return (
      "<div class='paper-card'><h2>Aprendizaje — SHADOW → BINDING</h2>"+notice+
      f"<p class='paper-muted'>Collector: {bg._e(state)} · {bg._e(data.get('detail',''))}. "
      "Camino: COLLECTING_EVIDENCE → SHADOW → VALIDATION → ELIGIBLE_FOR_BINDING_DECISION → autorización/release → BINDING_PAPER → validación → governance real-money.</p>"
      + ''.join(cards)+"</div>"
    )
