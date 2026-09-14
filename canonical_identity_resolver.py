"""Offline canonical instrument identity resolver for Wave A.

Inputs must already be mapped by an explicit provider adapter into the
normalized field names below. This module does not scrape, fetch, infer from a
ticker, query a database, or enable orders/readiness.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping


_REQUIRED_FIELDS = ("family", "ticker", "market", "venue", "currency", "settlement")
_OPTIONAL_DISCRIMINATORS = ("underlying", "issuer", "share_class")
_SUPPORTED_FAMILIES = frozenset({
    "ACCIONES", "ACCIONES_USA", "CEDEARS", "ETF", "BONOS", "LETRAS", "LEBAC",
    "NOBAC", "ON", "CAUCIONES", "OPCIONES", "FUTUROS", "FCI", "FCI_LOCAL",
    "FCI_EXTERIOR", "LICITACIONES", "INDICES", "CANJES",
})


def _plain(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().upper())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _family(value: object) -> str:
    token = re.sub(r"[^A-Z0-9]", "", _plain(value))
    aliases = {
        "ACCION": "ACCIONES",
        "EQUITY": "ACCIONES",
        "ACCIONESUSA": "ACCIONES_USA",
        "CEDEAR": "CEDEARS",
        "ETF": "ETF",
        "ETFS": "ETF",
        "BONO": "BONOS",
        "TITULOSPUBLICOS": "BONOS",
        "LETRA": "LETRAS",
        "ON": "ON",
        "OBLIGACION": "ON",
        "OBLIGACIONES": "ON",
        "OBLIGACIONESNEGOCIABLES": "ON",
        "CAUCION": "CAUCIONES",
        "OPCION": "OPCIONES",
        "OPTIONS": "OPCIONES",
        "FUTURO": "FUTUROS",
        "FUTURES": "FUTUROS",
        "FCIEXTERIOR": "FCI_EXTERIOR",
    }
    normalized = aliases.get(token, token)
    return normalized if normalized in _SUPPORTED_FAMILIES else ""


def _field_value(field: str, value: object) -> str:
    text = _plain(value)
    if not text:
        return ""
    if field == "family":
        return _family(value)
    if field == "currency":
        aliases = {
            "PESOS": "ARS",
            "PESO ARGENTINO": "ARS",
            "DOLARES BILLETE | MEP": "USD_MEP",
            "DOLARES DIVISA | CCL": "USD_CCL",
            "USD MEP": "USD_MEP",
            "USD CCL": "USD_CCL",
        }
        text = aliases.get(text, text)
    if field == "settlement":
        return re.sub(r"\s+", "_", text)
    return text


@dataclass(frozen=True)
class CanonicalIdentity:
    family: str
    ticker: str
    market: str
    venue: str
    currency: str
    settlement: str
    underlying: str = ""
    issuer: str = ""
    share_class: str = ""


@dataclass(frozen=True)
class IdentityResolution:
    status: str
    identity: CanonicalIdentity | None
    canonical_id: str | None
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]
    provider_ids: tuple[tuple[str, tuple[str, ...]], ...]

    def to_dict(self) -> dict:
        result = asdict(self)
        if self.identity is not None:
            result["identity"] = asdict(self.identity)
        result["missing"] = list(self.missing)
        result["conflicts"] = list(self.conflicts)
        result["provider_ids"] = {source: list(ids) for source, ids in self.provider_ids}
        return result


def resolve_canonical_identity(records: Iterable[Mapping[str, object]]) -> IdentityResolution:
    """Resolve identity only when independent normalized claims agree.

    Required identity includes family, ticker, market, venue, currency, and
    settlement. A ticker alone is never a join key. Conflicting values or
    multiple IDs from one provider fail closed; optional discriminators are
    retained and conflicts in them also block resolution.
    """
    if records is None:
        records = ()
    if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
        raise TypeError("records must be an iterable of normalized mappings")

    claims: dict[str, set[str]] = {field: set() for field in _REQUIRED_FIELDS + _OPTIONAL_DISCRIMINATORS}
    ids_by_source: dict[str, set[str]] = {}
    malformed = False
    for record in records:
        if not isinstance(record, Mapping):
            malformed = True
            continue
        for field in claims:
            value = _field_value(field, record.get(field))
            if value:
                claims[field].add(value)
        source = _plain(record.get("source"))
        provider_id = _plain(record.get("provider_id"))
        if provider_id and not source:
            malformed = True
        elif source and provider_id:
            ids_by_source.setdefault(source, set()).add(provider_id)

    conflicts = {field for field, values in claims.items() if len(values) > 1}
    if malformed:
        conflicts.add("malformed_record")
    if any(len(values) > 1 for values in ids_by_source.values()):
        conflicts.add("provider_id")

    missing = tuple(sorted(field for field in _REQUIRED_FIELDS if not claims[field]))
    provider_ids = tuple(sorted((source, tuple(sorted(values))) for source, values in ids_by_source.items()))
    if conflicts:
        return IdentityResolution("CONFLICT", None, None, missing, tuple(sorted(conflicts)), provider_ids)
    if missing:
        return IdentityResolution("INSUFFICIENT", None, None, missing, (), provider_ids)

    fields = {field: next(iter(claims[field])) if claims[field] else "" for field in claims}
    identity = CanonicalIdentity(**fields)
    canonical_payload = json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))
    canonical_id = "ci:v1:" + hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return IdentityResolution("RESOLVED", identity, canonical_id, (), (), provider_ids)
