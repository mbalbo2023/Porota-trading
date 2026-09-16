"""Canonical UX/navigation model for HF6 v2.

Pure definitions only: no runtime mutation, no order routing, no broker access.
Legacy URLs remain compatible. RC4 explicitly restores Scalping as a top-level
destination and adds a top-level Validation dashboard while keeping all trading
families visible under Trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class NavItem:
    href: str
    label: str


TOP_NAV = (
    NavItem("/", "Panel"),
    NavItem("/en-vivo", "En vivo"),
    NavItem("/trading", "Trading"),
    NavItem("/scalping", "Scalping"),
    NavItem("/validacion", "Validación"),
    NavItem("/instrumentos", "Instrumentos y contratos"),
    NavItem("/riesgo", "Riesgo"),
    NavItem("/historicos", "Históricos"),
    NavItem("/aprendizaje", "Aprendizaje"),
    NavItem("/reportes", "Reportes"),
    NavItem("/sistema", "Sistema"),
)

TRADING_NAV = (
    NavItem("/trading", "Resumen y motor"),
    NavItem("/trading/estrategias", "Estrategias"),
    NavItem("/trading/acciones-cedears", "Acciones y CEDEAR"),
)

# El alcance operativo de RC6 es explícito: no se exploran ni procesan otras
# familias hasta que haya una decisión de producto que las re-habilite.
OPERATIONAL_FAMILIES = frozenset(("ACCIONES", "CEDEARS"))

# Legacy routes are kept intentionally so bookmarks and old links do not break.
LEGACY_ROUTE_REDIRECTS = {
    "/motor-trading": "/trading",
    "/informacion-financiera": "/instrumentos",
}

FAMILY_GROUPS = {
    "acciones-cedears": ("ACCIONES", "CEDEARS"),
    "renta-fija": ("BONOS", "LETRAS", "ON"),
    "cauciones": ("CAUCIONES",),
    "opciones": ("OPCIONES",),
    "futuros": ("FUTUROS",),
    "fci": ("FCI", "FCI_LOCAL", "FCI_EXTERIOR"),
    "licitaciones": ("LICITACIONES", "CANJES"),
}

FAMILY_LABELS = {
    "ACCIONES": "Acciones",
    "CEDEARS": "CEDEAR",
    "BONOS": "Bonos",
    "LETRAS": "Letras",
    "ON": "Obligaciones Negociables",
    "CAUCIONES": "Cauciones",
    "OPCIONES": "Opciones",
    "FUTUROS": "Futuros",
    "FCI": "FCI",
    "FCI_LOCAL": "FCI local",
    "FCI_EXTERIOR": "FCI exterior",
    "LICITACIONES": "Licitaciones",
    "CANJES": "Canjes",
    "ETF": "ETF",
    "ACCIONES_USA": "Acciones USA",
}


def nav_html(items: Iterable[NavItem], *, css_class: str = "") -> str:
    cls = f" class='{css_class}'" if css_class else ""
    return "<nav" + cls + ">" + "".join(
        f"<a href='{item.href}'>{item.label}</a>" for item in items
    ) + "</nav>"


def top_nav_html() -> str:
    return "<nav id='porota-canonical-nav'>" + "".join(
        f"<a href='{item.href}'>{item.label}</a>" for item in TOP_NAV
    ) + "</nav>"


def trading_nav_html() -> str:
    return nav_html(TRADING_NAV, css_class="subnav")


def families_for_group(group: str) -> tuple[str, ...]:
    families = FAMILY_GROUPS.get(str(group or "").lower(), ())
    return tuple(family for family in families if family in OPERATIONAL_FAMILIES)


def assert_ux_invariants() -> None:
    hrefs = [item.href for item in TOP_NAV]
    if len(hrefs) != len(set(hrefs)):
        raise AssertionError("duplicate top-level navigation route")
    if "/scalping" not in hrefs:
        raise AssertionError("Scalping must be top-level in RC4")
    if "/validacion" not in hrefs:
        raise AssertionError("Validation destination missing")
    if "/trading" not in hrefs or "/instrumentos" not in hrefs:
        raise AssertionError("canonical Trading/Instrumentos destinations missing")
    if families_for_group("acciones-cedears") != ("ACCIONES", "CEDEARS"):
        raise AssertionError("operational actions/CEDEAR scope missing")
    if families_for_group("futuros"):
        raise AssertionError("non-operational families must not enter Trading navigation")
