# WS-MOTOR-11 — reconciliación #357/#358 y RCA

## Base real y ramas previas

- HEAD productivo revalidado: `a03142bc440bbb871714e4ae931d73513d073a56`.
- PR #357: draft/open, head `98ecf90859fe33dd66986b70f1c0010b0321fdb9`.
- PR #358: draft/open, head `17aa11e...` (WS09 publicado).
- El supuesto commit WS10 `3eddbd0d...` sólo existió localmente. No se presenta
  como objeto de GitHub, no se lo usa como base remota y no se falsifica CI.

La reconstrucción parte del HEAD real, incorpora el trabajo publicable de WS09
y recrea por código/test los invariantes documentados de WS10.

## Decisiones de reconciliación

| Tema | #357 | WS09/WS10 documentado | Resolución fix-forward |
|---|---|---|---|
| identidad primaria ambigua | permitía desambiguar con complementaria | aceptado sólo para no crear falsa ambigüedad | se conserva el fix estrecho |
| TTL identidad 14 días | propuesto | rechazado; identidad 24 h | vuelto a 86400 s |
| capture timestamp como price timestamp | permitido | rechazado | capture queda en provenance; dinámica exige provider timestamp |
| suprimir shadows por ticker/familia | propuesto | rechazado | se conserva complemento por full identity |
| clave Evidence v2 | omitía currency | WS10 exigía currency+settlement | migración forward-only, backup auditado y reconstrucción desde snapshots |
| reglas de readiness | dos matrices divergentes | autoridad única | `cq_family_contract_rules_hf6`; la otra queda fachada sin requirements propios |
| TTL estático | global | WS10: sólo dinámica vence | TTL por campo dinámico; estáticos versionados/hash |
| eventos futuros | bloqueaban toda apertura | OPEN/CLOSE separados de EVENT/FULL | perfiles canónicos y `EVENT_CONDITIONAL` |
| balance/permisos reales | podían bloquear PAPER | falsos requirements | excluidos de todo perfil PAPER |

## RCA de falsos blockers

1. Identidad incompleta: currency no estaba en la PK; una variante podía pisar
   otra. Fix: clave completa y migración desde historia append-only.
2. Dos autoridades de requisitos: campos con nombres/semánticas distintas
   producían estados contradictorios. Fix: una sola matriz y adaptador legacy.
3. Freshness plana: un contrato estático viejo aparecía stale o una captura
   reciente rejuvenecía dinámica. Fix: estáticos por hash/effective date;
   dinámica por provider timestamp y TTL de campo.
4. Lifecycle mezclado con OPEN: cupón/amortización/ejercicio futuro bloqueaba
   una simulación de apertura. Fix: perfiles, sin relajar EVENT/FULL.
5. Cuenta real mezclada con PAPER: balance/permisos/collateral no son inputs del
   ledger PAPER. Fix: exclusión explícita y test.
6. Blocker de executor mezclado con dato: FCI/futuros podían parecer “sin dato”
   aun con parte contractual. Fix: `blocker_axes` independientes y lifecycle
   PAPER aislado/idempotente, todavía no marcado integrado.
7. Requisitos sin consumidor: ISIN, `fee_schedule`, `trading_session`, tick y
   analytics bloqueaban OPEN aunque el motor usa la clave PPI, el tarifario
   central, `PaperSessionPolicy` y el libro vivo. Fix: matriz trazada a
   `InstrumentContract`/`PaperBroker`; esos campos pasan a enrichment.
8. PPI DatosTecnicos desaprovechado: `laminaMinima`, `multiploMinimo` y
   `nominalesEnPrecio` se guardaban pero se convertían en `None`. Fix: sólo esos
   campos explícitos —nunca los decimales de display— alimentan mínimo, step,
   base y multiplicador para la identidad exacta.
9. Parciales IOL descartados: si la chain/simulador no formaba un contrato
   completo, se perdían strike/expiry/maturity antes del merge. Fix: persistir
   parciales y completar campo a campo con PPI/oficial/DOM.

## Seguridad estructural

- `rc6_broker_parity_evidence.REAL_ROUTES_USED == ()`.
- `rc6_paper_family_lifecycle.PAPER_ONLY == True` y no contiene cliente broker.
- La migración sólo actúa sobre el `store` suministrado por el caller.
- Replay sobre copia temporal, SHA original before/after idéntico.
- No rollback, deploy ni merge.
