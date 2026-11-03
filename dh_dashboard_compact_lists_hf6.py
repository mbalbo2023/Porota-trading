"""Compact/paginated dashboard list helpers for HF6-v2.

Pure presentation helpers. They intentionally cap rendered rows so dashboard
pages do not become multi-hundred-record documents on tablets/mobile.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class Page:
    items: tuple
    offset: int
    limit: int
    total: int
    previous_offset: int | None
    next_offset: int | None


def paginate(items, *, offset=0, limit=DEFAULT_PAGE_SIZE) -> Page:
    rows=tuple(items or ())
    offset=max(0,int(offset))
    limit=max(1,min(MAX_PAGE_SIZE,int(limit)))
    total=len(rows)
    if offset >= total and total:
        offset=((total-1)//limit)*limit
    page=rows[offset:offset+limit]
    previous=max(0,offset-limit) if offset>0 else None
    nxt=offset+limit if offset+limit<total else None
    return Page(page,offset,limit,total,previous,nxt)


def pager_html(base_path: str, page: Page, *, extra_params=None) -> str:
    extra=dict(extra_params or {})
    links=[]
    if page.previous_offset is not None:
        q={**extra,"offset":page.previous_offset,"limit":page.limit}
        links.append(f"<a class='paper-action' href='{base_path}?{urlencode(q)}'>Anterior</a>")
    start=0 if page.total==0 else page.offset+1
    end=min(page.total,page.offset+len(page.items))
    links.append(f"<span class='paper-muted'>Mostrando {start}-{end} de {page.total}</span>")
    if page.next_offset is not None:
        q={**extra,"offset":page.next_offset,"limit":page.limit}
        links.append(f"<a class='paper-action' href='{base_path}?{urlencode(q)}'>Siguiente</a>")
    return "<nav class='compact-pager' aria-label='Paginación'>"+"".join(links)+"</nav>"


def key_value_cards(rows, *, title_key, fields, empty_text="Sin registros.") -> str:
    """Render generic operational rows vertically instead of wide tables."""
    if not rows:
        return f"<div class='paper-muted'>{empty_text}</div>"
    cards=[]
    for row in rows:
        title=str(row.get(title_key) or "—")
        body="".join(
            f"<div class='compact-field'><span>{label}</span><b>{row.get(key,'—')}</b></div>"
            for key,label in fields
        )
        cards.append(f"<article class='compact-record-card'><h3>{title}</h3>{body}</article>")
    return "<div class='compact-record-grid'>"+"".join(cards)+"</div>"


COMPACT_CSS = """
<style id='porota-hf6-v2-compact-lists'>
.compact-pager{display:flex;gap:10px;align-items:center;justify-content:center;flex-wrap:wrap;margin:14px 0}
.compact-record-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:10px}
.compact-record-card{border:1px solid var(--line);border-radius:10px;padding:11px;min-width:0;background:#fff;overflow-wrap:anywhere}
.compact-record-card h3{margin:0 0 8px;font-size:1rem}
.compact-field{display:flex;justify-content:space-between;gap:10px;border-top:1px solid var(--line);padding:6px 0;align-items:flex-start}
.compact-field span{color:var(--muted);min-width:0}.compact-field b{text-align:right;min-width:0;overflow-wrap:anywhere}
@media(max-width:700px){.compact-record-grid{grid-template-columns:1fr}.compact-field{display:block}.compact-field b{display:block;text-align:left;margin-top:2px}}
</style>
"""


def assert_compact_list_invariants():
    rows=list(range(200))
    page=paginate(rows,offset=0,limit=200)
    if len(page.items)>MAX_PAGE_SIZE:
        raise AssertionError("dashboard page can render too many list records")
    if "overflow-x" in COMPACT_CSS:
        raise AssertionError("compact-card primary UX must not rely on horizontal scrolling")
