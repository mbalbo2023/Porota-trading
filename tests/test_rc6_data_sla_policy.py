import pytest,rc6_data_sla_policy as sla
def test_default(monkeypatch):
    for k in ('PPI_BACKGROUND_INGEST_SECONDS','PPI_BACKGROUND_INGEST_WARN_AFTER_SECONDS','PPI_BACKGROUND_INGEST_MAX_STALENESS_SECONDS'): monkeypatch.delenv(k,raising=False)
    x=sla.ppi_background_ingest_sla(); assert (x.cadence_seconds,x.warning_after_seconds,x.max_staleness_seconds)==(21600,21600,21600)
def test_independent(monkeypatch):
    monkeypatch.setenv('PPI_BACKGROUND_INGEST_SECONDS','21600'); monkeypatch.setenv('PPI_BACKGROUND_INGEST_WARN_AFTER_SECONDS','25200'); monkeypatch.setenv('PPI_BACKGROUND_INGEST_MAX_STALENESS_SECONDS','43200'); x=sla.ppi_background_ingest_sla(); assert (x.cadence_seconds,x.warning_after_seconds,x.max_staleness_seconds)==(21600,25200,43200)
def test_invalid(monkeypatch):
    monkeypatch.setenv('PPI_BACKGROUND_INGEST_SECONDS','21600'); monkeypatch.setenv('PPI_BACKGROUND_INGEST_WARN_AFTER_SECONDS','7200')
    with pytest.raises(ValueError,match='WARNING_BEFORE_CADENCE'): sla.ppi_background_ingest_sla()
def test_no_2h_default(): assert 'PPI_BACKGROUND_INGEST_SECONDS", "7200"' not in open('bg_paper_dashboard.py').read()
