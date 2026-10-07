"""DOM acceptance against the fresh native page returned over stdio.

Only observation metadata is retained for assertions. No HTML/native response
is reused as a render, no producer input is changed and no stage is recomputed.
"""
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit

from tests.rc6_browser_ipc import GateFailure, require


def observe_health(product, *, require_live=False):
    """Preserve the canonical response/error and the complete 2s IPC bound."""
    try:
        native = product.health()
    except GateFailure as error:
        require(str(error) != "HEALTH_EXCEEDS_REQUEST_BUDGET" and product.last_elapsed <= 2,
                "HEALTH_EXCEEDS_REQUEST_BUDGET", {"elapsed_seconds": product.last_elapsed})
        # A missing/stopped/stale canonical producer is an honest conservative
        # observation. Transport failure or another native error is not health
        # evidence and must reject the entire browser gate.
        if str(error) != "NATIVE_BROWSER_REJECTED" or error.details.get("remote_error_class") not in {
                "FileNotFoundError", "ShadowHealthRejected"}:
            raise
        require(not require_live, "NATIVE_LIVE_HEALTH_NOT_VERIFIED",
                {"native_gate": str(error), "native_details": error.details})
        return {"consumer_state": "NO_VERIFICADO", "native_gate": str(error),
                "native_details": error.details, "request_wall_seconds_including_ipc": product.last_elapsed,
                "runtime_alive_claim": False, "health_authority": "NO_VERIFICADO"}
    require(not require_live or native.get("status") == "GREEN", "NATIVE_LIVE_HEALTH_NOT_VERIFIED")
    return {"consumer_state": native.get("status", "NO_VERIFICADO"), "native_result": native,
            "request_wall_seconds_including_ipc": product.last_elapsed,
            "runtime_alive_claim": native.get("status") == "GREEN",
            "health_authority": native.get("provider_capacity_open", "NO_VERIFICADO")}


def verified_scope(observed, family, offset):
    require(observed and observed["path"] == "/en-vivo" and observed["filters"].get("family") == family,
            "FAMILY_PAGE_WAS_NOT_FRESH_NATIVE_RENDER")
    scope = observed.get("native_funnel_page")
    require(type(scope) is dict and scope["state"] == "AVAILABLE" and scope["groups_offset"] == offset,
            "FAMILY_COHORT_PAGE_UNAVAILABLE")
    cut = datetime.fromisoformat(observed["source_cut"].replace("Z", "+00:00"))
    clock = datetime.fromisoformat(scope["as_of"].replace("Z", "+00:00"))
    require(cut.tzinfo is not None and clock == cut
            and scope["generation_id"] == observed["pointer"]["generation_id"], "FAMILY_PAGE_CLOCK_OR_CUT_CHANGED")
    require(type(offset) is int and type(scope["total_groups"]) is int
            and 0 <= offset < scope["total_groups"]
            and type(scope["groups"]) is list and type(scope["selected"]) is dict and type(scope["counts"]) is dict
            and len(scope["groups"]) == min(10, scope["total_groups"]-offset), "FAMILY_PAGE_COUNT_OR_LIMIT_CHANGED")
    rows = [scope["selected"], *(group["row"] for group in scope["groups"])]
    require(all(type(row) is dict and type(row.get("identity")) is list and len(row["identity"]) == 5
                and all(type(value) is str and value for value in row["identity"])
                and row["identity"] == [row.get(key) for key in ("symbol", "family", "settlement", "currency", "market")]
                and row["family"] == family and type(row.get("channel")) is str and row["channel"]
                and all(row.get(key) == observed["filters"][key]
                        for key in ("currency", "channel") if observed["filters"].get(key))
                for row in rows), "FAMILY_PAGE_IDENTITY_MISMATCH")
    return scope


def verify_family_pages(page, catalog_families, latest):
    """Observe first/second/final pages for every genuinely published family.

    Small native populations are traversed in full and explicitly lack a
    three-page claim. The proven DLR2026 contract is never enlarged for a test.
    """
    results = []
    require(type(catalog_families) is dict and catalog_families, "NATIVE_CATALOG_FAMILIES_REQUIRED")
    for family, catalog_count in sorted(catalog_families.items()):
        page.goto("http://terminal.test/en-vivo?"+urlencode({"family": family}))
        observed = latest()
        first = verified_scope(observed, family, 0)
        total, cut, pointer = first["total_groups"], observed["source_cut"], observed["pointer"]
        offsets = [0, 10, total-10] if total >= 30 else list(range(0, total, 10))
        aggregate, selected, previous_ids, pages = first["counts"], first["selected"], set(), []
        selected_dom = page.locator("ol.funnel").inner_text()
        for offset in offsets:
            if offset:
                page.goto("http://terminal.test/en-vivo?"+urlencode({"family": family, "funnel_offset": offset}))
            observed = latest()
            scope = verified_scope(observed, family, offset)
            require(observed["source_cut"] == cut and observed["pointer"] == pointer
                    and scope["total_groups"] == total and scope["counts"] == aggregate
                    and scope["selected"] == selected, "FAMILY_PAGING_CHANGED_SELECTION_OR_AGGREGATES")
            require(page.locator("ol.funnel").inner_text() == selected_dom, "FAMILY_DOM_AGGREGATES_CHANGED")
            anchors = page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']")
            hrefs = [anchors.nth(index).get_attribute("href") for index in range(anchors.count())]
            ids = [parse_qs(urlsplit(href).query)["cohort"][-1] for href in hrefs]
            expected = [group["cohort"] for group in scope["groups"]]
            require(ids == expected and len(set(ids)) == len(ids) and not previous_ids.intersection(ids),
                    "FAMILY_DOM_ORDER_OVERLAP_OR_COHORT_ID_CHANGED")
            previous_ids.update(ids)
            require(observed["response_json_bytes"] < 4*1024**2
                    and observed["request_wall_seconds_including_ipc_html_json"] <= 1, "FAMILY_PAGE_REQUEST_BUDGET")
            pages.append({"offset": offset, "cohort_ids": ids, "source_cut": cut, "total_groups": total,
                          "request_wall_seconds_including_ipc_html_json": observed["request_wall_seconds_including_ipc_html_json"],
                          "response_json_bytes": observed["response_json_bytes"]})
        chosen = scope["groups"][-1]
        anchors.last.click()
        exact = verified_scope(latest(), family, 0)
        require(exact["total_groups"] == 1 and exact["selected"] == chosen["row"]
                and exact["groups"][0]["cohort"] == chosen["cohort"]
                and latest()["pointer"] == pointer and latest()["source_cut"] == cut,
                "FAMILY_LAST_EXACT_COHORT_UNREACHABLE")
        require("funnel_offset=" not in page.url and "grupos 1–1 de 1" in
                page.locator("section.panel:has(ol.funnel)").inner_text(), "FAMILY_EXACT_SELECTION_DOM_CHANGED")
        narrowed = {"family": family, "cohort": chosen["cohort"],
                    "currency": chosen["row"]["currency"], "channel": chosen["row"]["channel"]}
        page.goto("http://terminal.test/en-vivo?"+urlencode(narrowed))
        scoped = verified_scope(latest(), family, 0)
        require(scoped["total_groups"] == 1 and scoped["selected"] == chosen["row"]
                and scoped["counts"] == exact["counts"] and latest()["pointer"] == pointer
                and latest()["source_cut"] == cut, "FAMILY_CURRENCY_CHANNEL_SCOPE_CHANGED_NATIVE_COHORT")
        results.append({"family": family, "catalog_rows": catalog_count, "cohort_total": total,
            "coverage": "THREE_NATIVE_PAGES" if len(offsets) >= 3 else "ALL_AVAILABLE_SMALL_NATIVE_GROUPS",
            "pages": pages, "exact_selected_cohort": chosen["cohort"],
            "selected_native_identity": chosen["row"]["identity"],
            "selected_currency": chosen["row"]["currency"], "selected_channel": chosen["row"]["channel"],
            "currency_channel_scope_total": scoped["total_groups"],
            "full_page_traversal_claimed": total < 30})
    return results
