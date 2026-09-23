import sqlite3
from types import SimpleNamespace

import rc6_official_source_adapters as m


class Store:
    def __init__(self, path): self.path = str(path)
    def connect(self): return sqlite3.connect(self.path)


def opener_factory(status=200, content_type="application/json", body=b""):
    def opener(request, timeout):
        return SimpleNamespace(status=status, headers={"Content-Type": content_type}, read=lambda limit: body)
    return opener


def test_json_contract_fields_are_normalized_without_inference():
    result = m.parse_payload("CNV", "https://official.test/data", 200, "application/json",
                             b'{"items":[{"symbol":"ON123","isin":"AR123","vencimiento":"2030-01-01","coupon_rate":8.5}]}')
    assert result.status == "REACHABLE_STRUCTURED"
    assert result.evidence["records"] == [{"source":"CNV","ticker":"ON123","isin":"AR123",
                                             "maturity":"2030-01-01","coupon":8.5}]


def test_html_is_reference_only_not_contract_ready():
    result = m.parse_payload("BYMA", "https://official.test", 200, "text/html", b"<title>BYMA</title>")
    assert result.status == "REFERENCE_ONLY"
    assert result.evidence["record_count"] == 0


def test_collect_persists_each_source_and_handles_http_error(tmp_path):
    store = Store(tmp_path / "evidence.sqlite")
    result = m.collect(store, opener=opener_factory(body=b'{"items":[{"ticker":"DLR","contractMultiplier":1000}]}'),
                       urls={"BYMA":"https://official.test/byma", "CNV":"https://official.test/cnv",
                             "MATBA_ROFEX":"https://official.test/rofex"})
    assert all(item["status"] == "REACHABLE_STRUCTURED" for item in result["sources"])
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM official_source_evidence").fetchone()[0] == 3
