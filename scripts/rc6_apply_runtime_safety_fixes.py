#!/usr/bin/env python3
"""Apply narrowly-scoped RC6 runtime safety fixes, idempotently."""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source pattern, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        "be_paper_engine.py",
        '                 daily_loss_pct="2.5", daily_soft_stop_pct=None,\n',
        '                 daily_loss_pct=None, daily_soft_stop_pct=None,\n',
    )
    replace_once(
        "bv_paper_runtime.py",
        '        economics_mode=os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING"),\n',
        '        economics_mode=os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW"),\n',
    )
    replace_once(
        "cf_sale_settlement.py",
        """    if basis=='PENDING_CONFIRMATION':\n        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')\n        return conservative_unconfirmed_availability(settlement,traded)\n""",
        """    if basis=='PENDING_CONFIRMATION':\n        if available is not None: raise ValueError('Recibo pendiente con acreditación no confirmada')\n        # Sin evidencia del broker no existe una hora de disponibilidad defendible.\n        # La fecha hábil esperada sirve para diagnóstico, jamás para liberar caja.\n        return None\n""",
    )
    print("RC6_RUNTIME_SAFETY_MIGRATION=APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
