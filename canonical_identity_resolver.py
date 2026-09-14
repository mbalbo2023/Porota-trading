"""Offline canonical instrument identity resolver for Wave A.

Provider adapters must map raw fields explicitly to the normalized names below.
This module has no I/O and does not infer identity from a ticker. It neither
checks readiness nor enables orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping


_REQUIRED_FIELDS = ("family", "subfamily", "ticker", "market", "venue", "currency", "settlement")
_IDENTITY_DETAILS = ("underlying", "issuer", "share_class", "expiry", "strike", "put_call")
_SUPPORTED_FAMILIES = frozenset({
    "ACCIONES", "ACCIONES_USA", "CEDEARS", "ETF", "BONOS", "LETRAS", "LEBAC",
    "NOBAC", "ON", "CAUCIONES", "OPCIONES", "FUTUROS", "FCI", "FCI_LOCAL",
    "FCI_EXTERIOR", "LICITACIONES", "INDICES", "CANJES",
})
_FAMILY_REQUIRED_DETAILS = {
    "CEDEARS": ("underlying",),
    "OPCIONES": ("underlying", "expiry", "strike", "put_call"),
    "FUTUROS": ("underlying", "expiry"),
}
_CANONICAL_SETTLEMENTS = frozenset({
    "CI", "CONTADO_INMEDIATO", "T+0", "T+1", "T+2", "T+3", "T0", "T1", "T2", "T3",
    "24_HORAS", "48_HORAS", "72_HORAS", "SPOT", "NON_INTRADAY", "NO_APLICA",
})


def _plain(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().upper())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _family_details(value: object) -> tuple[str, str]:
    token = re.sub(r"[^A-Z0-9]", "", _plain(value))
    aliases = {
        "ACCION": "ACCIONES",
        "EQUITY": "ACCIONES",
        "ACCIONESUSA": "ACCIONES_USA",
        "CEDEAR": "CEDEARS",
        "CEDEARETF": ("CEDEARS", "ETF"),
        "CEDEARETFS": ("CEDEARS", "ETF"),
        "ETFCEDEAR": ("CEDEARS", "ETF"),
        "ETF": "ETF",
        "ETFS": "ETF",
        "BONO": "BONOS",
        "TITULOSPUBLICOS": "BONOS",
        "LETRA": "LETRAS",
        "LETRALINKED": ("LETRAS", "LINKED"),
        "ON": "ON",
        "OBLIGACION": "ON",
        "OBLIGACIONES": "ON",
        "OBLIGACIONESNEGOCIABLES": "ON",
        "CAUCION": "CAUCIONES",
        "CAUCIONCOLOCADORA": ("CAUCIONES", "COLOCADORA"),
        "CAUCIONTOMADORA": ("CAUCIONES", "TOMADORA"),
        "OPCION": "OPCIONES",
        "OPTIONS": "OPCIONES",
        "FUTURO": "FUTUROS",
        "FUTURES": "FUTUROS",
        "FCIEXTERIOR": "FCI_EXTERIOR",
    }
    normalized = aliases.get(token, token)
    if isinstance(normalized, tuple):
        family, inline_subfamily = normalized
    else:
        family, inline_subfamily = normalized, ""
    if family not in _SUPPORTED_FAMILIES:
        return "", ""
    return family, inline_subfamily


def _field_value(field: str, value: object) -> str:
    text = _plain(value)
    if not text:
        return ""
    if field == "family":
        return _family_details(value)[0]
    if field == "subfamily":
        return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    if field == "currency":
        aliases = {
            "PESOS": "ARS",
            "PESO ARGENTINO": "ARS",
            "PESOS ARGENTINOS": "ARS",
            "DOLARES BILLETE | MEP": "USD_MEP",
            "DOLARES DIVISA | CCL": "USD_CCL",
            "USD MEP": "USD_MEP",
            "USD CCL": "USD_CCL",
        }
        text = aliases.get(text, text)
        if text in {"D", "C"} or not re.fullmatch(r"(?:[A-Z]{3}|USD_MEP|USD_CCL)", text):
            return ""
    if field == "settlement":
        normalized = re.sub(r"\\s+", "_", text)
        return normalized if normalized in _CANONICAL_SETTLEMENTS else ""
    if field in {"expiry", "put_call"}:
        return re.sub(r"\s+", "_", text)
    return text


@dataclass(frozen=True)
class CanonicalIdentity:
    family: str
    subfamily: str
    ticker: str
    market: str
    venue: str
    currency: str
    settlement: str
    underlying: str = ""
    issuer: str = ""
    share_class: str = ""
    expiry: str = ""
    strike: str = ""
    put_call: str = ""


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
        result["missing"] = list(self.missing)
        result["conflicts"] = list(self.conflicts)
        result["provider_ids"] = {source: list(ids) for source, ids in self.provider_ids}
        return result


def resolve_canonical_identity(records: Iterable[Mapping[str, object]]) -> IdentityResolution:
    """Resolve only explicit, provenance-backed identity claims that agree.

    Family subfamily is explicit and required. CEDEARs require an underlying;
    options require underlying/expiry/strike/put_call; futures require
    underlying/expiry. Provider IDs are retained as provenance and at least one
    source ID is required on every contributing record. Settlement uses a
    closed canonical vocabulary; unknown aliases remain unresolved until an
    evidence-backed adapter maps them. Optional descriptive enrichment does
    not change the canonical key, while conflicts in it still block resolution.
    """
    if records is None:
        records = ()
    if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
        raise TypeError("records must be an iterable of normalized mappings")

    claim_fields = _REQUIRED_FIELDS + _IDENTITY_DETAILS
    claims: dict[str, set[str]] = {field: set() for field in claim_fields}
    ids_by_source: dict[str, set[str]] = {}
    malformed = False
    for record in records:
        if not isinstance(record, Mapping):
            malformed = True
            continue
        for field in claim_fields:
            value = _field_value(field, record.get(field))
            if value:
                claims[field].add(value)
        family, inline_subfamily = _family_details(record.get("family"))
        if inline_subfamily:
            claims["subfamily"].add(_field_value("subfamily", inline_subfamily))
        source = _plain(record.get("source"))
        provider_id = str(record.get("provider_id") or "").strip()
        if not source or not provider_id:
            malformed = True
        else:
            ids_by_source.setdefault(source, set()).add(provider_id)

    conflicts = {field for field, values in claims.items() if len(values) > 1}
    if malformed:
        conflicts.add("malformed_record")
    if any(len(values) > 1 for values in ids_by_source.values()):
        conflicts.add("provider_id")

    required = set(_REQUIRED_FIELDS)
    family_values = claims["family"]
    if len(family_values) == 1:
        required.update(_FAMILY_REQUIRED_DETAILS.get(next(iter(family_values)), ()))
    missing = {field for field in required if not claims[field]}
    if not ids_by_source:
        missing.add("provider_id")

    missing_tuple = tuple(sorted(missing))
    provider_ids = tuple(sorted((source, tuple(sorted(values))) for source, values in ids_by_source.items()))
    if conflicts:
        return IdentityResolution("CONFLICT", None, None, missing_tuple, tuple(sorted(conflicts)), provider_ids)
    if missing_tuple:
        return IdentityResolution("INSUFFICIENT", None, None, missing_tuple, (), provider_ids)

    fields = {field: next(iter(claims[field])) if claims[field] else "" for field in claim_fields}
    identity = CanonicalIdentity(**fields)
    key_fields = sorted(required)
    canonical_payload = json.dumps(
        {field: getattr(identity, field) for field in key_fields},
        sort_keys=True,
        separators=(",", ":"),
    )
    canonical_id = "ci:v1:" + hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return IdentityResolution("RESOLVED", identity, canonical_id, (), (), provider_ids)
