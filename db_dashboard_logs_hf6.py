"""Read-only log discovery/tailing for the HF6 v2 / RC4 dashboard.

The dashboard never receives the Docker socket. A host exporter writes
sanitized runtime snapshots into the shared log directory and this module
allows only regular files under that directory to be viewed/downloaded.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os


@dataclass(frozen=True)
class LogSource:
    source_id: str
    label: str
    path: Path
    size_bytes: int
    modified_ns: int

    @property
    def modified_at(self) -> str:
        return datetime.fromtimestamp(self.modified_ns / 1_000_000_000, tz=timezone.utc).isoformat()


PREFERRED_FILES = (
    ("bot", "Bot / aplicación", "bot_runtime.log"),
    ("observer", "Observer runtime", "observer_runtime.log"),
    ("dashboard", "Dashboard runtime", "dashboard_runtime.log"),
    # Compatibilidad con instalaciones que todavía exponen el archivo rotado
    # original directamente en el volumen compartido.
    ("application", "Bot / aplicación (legacy)", "trading_bot.log"),
    ("scraping", "Scraping / Contract Evidence", "contract_evidence_runtime.log"),
)


def log_root() -> Path:
    return Path(os.getenv("POROTA_SHARED_LOG_DIR", os.getenv("LOG_DIR", "data/logs"))).resolve()


def _safe_regular(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        return resolved.is_relative_to(root) and resolved.is_file() and not resolved.is_symlink()
    except (OSError, RuntimeError):
        return False


def discover_sources(root: Path | None = None) -> list[LogSource]:
    base = (root or log_root()).resolve()
    sources: list[LogSource] = []
    seen: set[Path] = set()
    for source_id, label, filename in PREFERRED_FILES:
        p = base / filename
        if _safe_regular(p, base):
            st = p.stat()
            sources.append(LogSource(source_id, label, p.resolve(), st.st_size, st.st_mtime_ns))
            seen.add(p.resolve())
    if base.exists():
        for p in sorted(base.glob("*.log"), key=lambda x: x.name.lower()):
            if not _safe_regular(p, base) or p.resolve() in seen:
                continue
            st = p.stat()
            sources.append(LogSource(p.stem, p.name, p.resolve(), st.st_size, st.st_mtime_ns))
    return sources


def primary_source(root: Path | None = None) -> LogSource | None:
    sources = discover_sources(root)
    return sources[0] if sources else None


def source_by_id(source_id: str, root: Path | None = None) -> LogSource | None:
    wanted = str(source_id or "").strip()
    for source in discover_sources(root):
        if source.source_id == wanted:
            return source
    return None


def tail_lines(source: LogSource, lines: int = 50) -> list[str]:
    limit = max(1, min(int(lines), 500))
    try:
        # Snapshot logs are deliberately bounded by the host exporter, so a
        # simple read is predictable and avoids unsafe shell/tail execution.
        return source.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


def source_freshness(source: LogSource, *, now_ns: int | None = None,
                     stale_after_seconds: int = 180) -> str:
    """Classify freshness without turning an absent source into a false OK."""
    current = int(now_ns if now_ns is not None else datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    age = max(0, current - int(source.modified_ns)) / 1_000_000_000
    return "FRESH" if age <= max(1, int(stale_after_seconds)) else "STALE"


def assert_log_invariants() -> None:
    root = log_root()
    for source in discover_sources(root):
        if not source.path.is_relative_to(root):
            raise AssertionError("log path escaped configured root")
        if source.path.is_symlink():
            raise AssertionError("symlink log source is forbidden")
