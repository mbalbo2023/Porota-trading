"""A777 fail-first: a request's settlement is not native PPI Book1 authority.

The in-memory catalog is synthetic. No PPI connection or order is used.
This exercises the actual scanner observer functions and journal writer.
"""

from rc6_ppi_scanner_provenance import ScannerPrimaryRecorder, PIT_JOURNAL_FILENAME
from tests import test_rc6_a762_book1_fivekey_guard as a762


def test_request_candidate_without_native_settlement_cannot_mint_book1(tmp_path):
    store = a762.Store(tmp_path / "paper.db")
    with store.connect() as connection:
        connection.execute("""CREATE TABLE financial_instrument_catalog(
            ticker TEXT, instrument_type TEXT, settlement TEXT,
            market TEXT, currency TEXT, settlement_source TEXT)""")
        connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?)",
            ("GGAL", "ACCIONES", "A-24HS", "BYMA", "ARS", "REQUEST_CANDIDATE"))

    journal_dir = tmp_path / "market"
    journal_dir.mkdir()
    recorder = ScannerPrimaryRecorder(journal_dir / "latest.json")
    journal = journal_dir / PIT_JOURNAL_FILENAME
    reader = a762.Reader()
    read = a762.load_real_observer_functions()
    quote = read(reader, store, "GGAL", "ACCIONES", "A-24HS",
                 identity=a762.IDENTITY, priority="DISCOVERY", scanner_primary=recorder)

    # Safety behavior: Current/Book still serve READ_ONLY scanner diagnostics,
    # but a user-requested settlement cannot manufacture provider attestation.
    assert reader.calls == [
        ("current", ("GGAL", "ACCIONES", "A-24HS")),
        ("book", ("GGAL", "ACCIONES", "A-24HS")),
    ]
    assert quote.ppi_book1_event_sha256 is None
    assert not journal.exists()
