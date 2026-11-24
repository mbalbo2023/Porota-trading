# Cobertura histórica de Porota RC6

El inventario contiene las 17.351 claves observadas en el catálogo del Droplet el 23/11/2026 ART. El CSV de cada familia conserva ticker, familia, plaza, moneda, plazo y estado de catálogo. Cada referencia PPI incluye el selector registrado, la cantidad de fechas y la última fecha. Las referencias complementarias se registran por símbolo y familia. La moneda y el plazo de estas últimas quedan sin acreditar.

- El histórico acumula 736.258 filas `SOURCE_REPORTED`. Esta recuperación añadió 584.206.
- PPI aportó 577.302 filas, IOL aportó 6.902 e Intervalores aportó 2.
- Hay 5.767 fichas con referencia. Entre las 10.456 disponibles, 3.485 tienen referencia y 6.971 quedan sin ella.
- El corte RAW queda pendiente. Se preservaron las 439.833 canónicas y 596.196 versiones del almacén legado.
- El ZIP `cobertura-por-instrumento-2026-11-23.zip` tiene SHA-256 `7c1c8f85e1bb2e2a874ba74b0874635e65691cdb935b817afe6c8ce56a5788b8`. El JSONL de origen tiene SHA-256 `bdb4248aec1cbaa00a6cbd86bb735c4cab9fb095d6cde3d69351dd6f2fd0e1ff`.

Los recuentos provienen de SQLite en el runtime `bf496483e739c5a2be61d28aba6ad5fdd486c0e1`, en PRODUCTION_PAPER / SIMULATION con `real_orders_sent=0`. Todas las cargas devolvieron `quick_check=ok`.

[Deploy V2 y carga inicial](https://github.com/mbalbo2023/Porota-trading/actions/runs/45303731515/artifacts/14881716376) · [Compatibilidad de cierre](https://github.com/mbalbo2023/Porota-trading/actions/runs/45308141174/artifacts/14882566632) · [Renta fija vía IOL](https://github.com/mbalbo2023/Porota-trading/actions/runs/45308909871/artifacts/14883101275) · [Cuatro FCI y cobertura final](https://github.com/mbalbo2023/Porota-trading/actions/runs/45310318363/artifacts/14883750418)
