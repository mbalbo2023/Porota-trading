# POROTA TRADING — CORRECCIÓN CANÓNICA DE TABLAS DASHBOARD RC6 — 2026-09-06

Este archivo corrige la interpretación visual aplicada durante el rediseño del dashboard RC6 y debe leerse junto con el checkpoint canónico y el archivo de pendientes.

## Requerimiento correcto

Cuando el usuario pide que una sección “sea una tabla”, se refiere al diseño clásico que POROTA tenía antes del rediseño: **grilla real de filas y columnas**.

El diseño actual que transforma cada registro en una tarjeta/bloque apilado con pares etiqueta/valor no es aceptado para operación en tablet porque resulta ilegible y dificulta comparar registros.

## Diseño requerido para mañana

- HTML/tablas visuales con encabezados de columna persistentes/claros.
- Una fila por registro.
- Cada métrica mantiene la misma columna para todos los registros.
- No repetir etiquetas de columna dentro de cada celda como patrón principal.
- No transformar automáticamente las filas en cards en Samsung/tablet por ancho de viewport/contenedor.
- Mantener la comparación horizontal propia de una tabla.
- Preservar contraste, tipografía, altura táctil y Voice Access.
- Sin scroll horizontal global de toda la página.
- Si una grilla excepcionalmente necesita más ancho, priorizar columnas o limitar el scroll al contenedor de esa tabla; no reemplazar la grilla por cards.

## Superficies a restaurar/revisar

1. Caja y patrimonio por moneda.
2. Histórico de trading.
3. Universo operativo / matriz por familia.
4. Scalping / contrato intradiario.
5. Instrumentos y contratos.
6. Aprendizaje / cobertura empírica.
7. Salud de APIs.
8. Jobs internos.
9. Scraping.
10. Backups.
11. Cualquier otra pseudo-tabla convertida a cards durante el rediseño.

## Criterio de aceptación

- En la tablet Samsung se ve una tabla real de filas y columnas.
- La lectura comparativa entre registros es inmediata.
- Voice Access sigue funcionando.
- No se cambian queries, source-of-truth ni semántica de las tarjetas/indicadores.
- El cambio es dashboard-only.
- Observer `db26c76723bb988c956589c572b87cbcb4191731` no se reinicia ni se modifica.
- Estrategia/gates no cambian.
- Real order capability permanece BLOCKED.

## Prioridad

`P1 INMEDIATO / UX OPERATIVA PARA 2026-09-07`.

No es un P0 de safety, pero sí una necesidad operativa para controlar adecuadamente la jornada crítica del lunes.
