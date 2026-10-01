# CHECKPOINT 00 — WS-DASH-06 — OCT-01

- branch: work/ws-dash-06-oct01-ux-truth-20260930
- base: 94b31b3a362f8d0802c1c5f800762c6d024deae1
- mode: WRITE_OWNER
- no deploy.
- scope: Dashboard/UX/truth visible.
- implementado para test:
  - ocho ruedas operativas, calendario hábil;
  - búsqueda/filtros server-side de Instrumentos;
  - búsqueda/filtros de Universo Operativo;
  - Evidence v2 con timestamp observado;
  - estado general detalla verificaciones pendientes;
  - statuses largos wrap;
  - cabeceras no sticky/sin superposición;
  - coloración semántica global de columnas PnL/resultado/retorno;
  - cutoff explícito para observaciones fuera de catálogo.
- pendientes de este scope antes de PR: Action GREEN + runtime probe después de integración.
