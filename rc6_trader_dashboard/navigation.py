"""Eight destinations and explicit, deep-linkable subviews."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Destination:
    key: str
    label: str
    path: str
    icon: str
    question: str
    tabs: tuple[tuple[str, str], ...]


DESTINATIONS = (
    Destination("inicio", "Inicio", "/", "◈", "¿Cómo estoy y qué exige atención ahora?",
                (("resumen", "Estado & atención"),)),
    Destination("en-vivo", "En Vivo", "/en-vivo", "◉", "¿Qué está haciendo POROTA en esta rueda y por qué?", (
        ("resumen", "Resumen"), ("oportunidades", "Oportunidades"), ("decisiones", "Decisiones"),
        ("posiciones", "Posiciones"), ("cierres", "Cierres"), ("capacidad", "Capacidad & Coverage"),
        ("workers", "Riesgo & Workers"), ("fuentes", "Fuentes"))),
    Destination("trading", "Trading", "/trading", "⇄", "¿Qué estrategias están activas y cómo opera cada familia?", (
        ("resumen", "Resumen"), ("equity-spot", "Equity Spot"), ("scalping", "Scalping"),
        ("renta-fija", "Renta Fija"), ("derivados", "Derivados"), ("tesoreria", "Tesorería / FCI"))),
    Destination("universo", "Universo", "/universo", "◇", "¿Qué parte del mercado ve, prioriza o excluye POROTA?", (
        ("resumen", "Resumen"), ("discovery", "Discovery"), ("tradeability", "Tradeability"),
        ("coverage", "Coverage"), ("excluidos", "Excluidos"), ("familias", "Familias"))),
    Destination("instrumentos", "Instrumentos", "/instrumentos", "⌕", "¿Qué sé exactamente de esta especie?", (
        ("buscador", "Buscador"), ("ficha", "Ficha"), ("contrato", "Contrato"),
        ("mercado", "Mercado & Liquidez"), ("evidencia", "Evidencia"))),
    Destination("riesgo", "Riesgo", "/riesgo", "△", "¿Cuánto puedo perder y qué puede quedar sin salida?", (
        ("resumen", "Resumen"), ("exposicion", "Exposición"), ("limites", "Límites"),
        ("posiciones", "Posiciones"), ("liquidez", "Liquidez & Salidas"),
        ("pnl", "P&L / Drawdown"), ("event-risk", "Event Risk"))),
    Destination("analitica", "Analítica", "/analitica", "▥", "¿El sistema tiene edge y qué está aprendiendo?", (
        ("performance", "Performance"), ("senales", "Señales"), ("salidas", "Salidas"),
        ("historico", "Histórico"), ("experimentos", "Experimentos"),
        ("cierre-diario", "Cierre diario"), ("reportes", "Reportes"))),
    Destination("sistema", "Sistema", "/sistema", "⚙", "¿La plataforma técnica está sana?", (
        ("resumen", "Resumen"), ("salud", "Salud"), ("workers", "Workers"),
        ("integraciones", "Integraciones"), ("scheduler", "Scheduler"), ("evidencia", "Evidencia"),
        ("backups", "Backups"), ("logs", "Logs"), ("configuracion", "Configuración"))),
)
BY_KEY = {item.key: item for item in DESTINATIONS}

# Bookmarks retain their URL and render the corresponding canonical subview.
LEGACY = {
    "/inicio": ("inicio", "resumen"), "/vivo": ("en-vivo", "resumen"),
    "/universo-operativo": ("universo", "resumen"), "/scalping": ("trading", "scalping"),
    "/motor-trading": ("trading", "resumen"), "/validacion": ("analitica", "experimentos"),
    "/historicos": ("analitica", "historico"), "/analisis": ("analitica", "performance"),
    "/aprendizaje": ("analitica", "experimentos"), "/reportes": ("analitica", "reportes"),
    "/postcierre": ("analitica", "cierre-diario"), "/salud": ("sistema", "salud"),
    "/sre": ("sistema", "salud"), "/infra": ("sistema", "resumen"),
    "/scheduler": ("sistema", "scheduler"), "/dashboard/logs": ("sistema", "logs"),
    "/config": ("sistema", "configuracion"), "/testing": ("sistema", "evidencia"),
    "/observacion": ("universo", "discovery"), "/telegram": ("sistema", "workers"),
    "/informacion-financiera": ("instrumentos", "evidencia"),
    "/ai-decisions": ("en-vivo", "decisiones"),
}
TAB_ALIASES = {"acciones": "equity-spot", "equity": "equity-spot", "cedears": "equity-spot",
               "bonos": "renta-fija", "opciones": "derivados", "futuros": "derivados",
               "cauciones": "tesoreria", "fci": "tesoreria", "introspeccion": "resumen",
               "scraping": "integraciones", "config": "configuracion"}


def view_path(destination, tab):
    return destination.path if tab == destination.tabs[0][0] else destination.path + "/" + tab


def resolve(path, query=None):
    query = query or {}
    if path in LEGACY:
        key, tab = LEGACY[path]
        return BY_KEY[key], tab
    for item in DESTINATIONS:
        if path == item.path or (item.path != "/" and path.startswith(item.path + "/")):
            tab = path[len(item.path):].strip("/") or query.get("view") or query.get("section") or item.tabs[0][0]
            tab = TAB_ALIASES.get(tab, tab)
            return item, tab if tab in dict(item.tabs) else None
    return None, None


CANONICAL_PATHS = {view_path(item, tab): (item.key, tab) for item in DESTINATIONS for tab, _ in item.tabs}
