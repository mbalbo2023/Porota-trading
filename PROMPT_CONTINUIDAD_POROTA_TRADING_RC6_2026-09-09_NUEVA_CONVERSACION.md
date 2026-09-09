# PROMPT DE CONTINUIDAD — POROTA TRADING RC6
## Nueva conversación

Quiero continuar POROTA TRADING RC6 exactamente desde la conversación anterior.

Te adjunto:
`POROTA_TRADING_CHECKPOINT_COMPLETO_RC6_2026-09-09_CONTINUIDAD_TOTAL.md`

Ese archivo es el CONTEXTO CANÓNICO.

## INSTRUCCIÓN ABSOLUTA

Antes de hacer absolutamente nada:
1. Lee COMPLETO el checkpoint.
2. Conéctate al repo privado `mbalbo2023/Porota-trading`.
3. Refresca GitHub Actions, ramas y runtime live.
4. Si hay evidencia posterior al checkpoint, úsala, pero conserva todas las decisiones y restricciones.
5. No me pidas reconstruir contexto.
6. No repitas pruebas cerradas.
7. No confundas CI GREEN con runtime GREEN.

## REGLAS DE OPERACIÓN

Tengo cuadriplejia y opero principalmente por voz desde tablet Android.

- Preferir GitHub Actions → Droplet.
- Termius sólo si es imprescindible.
- Si hay SSH, un solo bloque completo.
- Scripts `.sh`.
- `sudo -n`.
- No prompts interactivos.
- Salida compacta.
- No ZIP salvo pedido.
- Trabajar todo lo posible en paralelo.
- Informar hallazgos parciales.

## SEGURIDAD

Modo:
`PRODUCTION_PAPER`

Mantener siempre:
- real orders 0;
- no `/Operar`;
- no APIs de órdenes;
- no network order tests;
- no permission probes;
- no bypass 2FA/OTP;
- no secretos en logs;
- `ROLLBACK_AUTOMATICO=NO`.

## BASE LIVE

Último live conocido:
`9197aedf35fe1739b594c742902e80df069c47c9`

Verificar de nuevo antes de actuar.

## PRIORIDAD P0 — W12 SCRAPING / CONTRACT EVIDENCE

No empezar RCA desde cero.

Ya se corrigió:
- legacy RC4;
- Chrome ownership;
- telemetry;
- bug `set -e`;
- reauth handoff;
- `/Operar/*`;
- username fill;
- selectors.

Form actual:
- `#username`
- `#password`
- botón `Ingresar`

Último W12 live proof:
- run `34389232945`;
- static gate GREEN;
- isolated install GREEN;
- reauth arrancó;
- `FRESH_CAPTURE=NO`;
- CE runs `103 -> 103`;
- blocker final `BLOCKED_AUTH_PASSWORD_FILL_FAILED`;
- DB/PAPER/orders0 intactos.

Rama actual:
`deploy-prep/rc6-wave12-currentbase-20260909`

HEAD más nuevo conocido:
`d7cb02145f4a545fd67f374a42f4c73bc458ad90`

Próximo paso:
- verificar HEAD;
- correr UNA prueba controlada con el helper resiliente;
- no borrar profile/cookies/secrets;
- diagnostics sanitizados;
- exigir fresh capture + trusted auth + `/Cotizaciones/*` only + import + CE runs >103.

No poner W12 GREEN sin fresh capture e import.

## OLAS

Estado del checkpoint:
- W9 GREEN / CODE READY
- W10 GREEN / CODE READY, BINDING materializado
- W11 GREEN / CODE READY
- W12 YELLOW / P0
- W13 GREEN / CODE READY
- W14 GREEN / CODE READY
- W15 GREEN / CODE READY
- W16 GREEN / READY
- W17 GREEN / LIVE GREEN
- W18 GREEN / CODE READY V4 sin auto rollback

Objetivo:
llevar todo a un candidate consolidado current-base.

No mergear ramas históricas completas.
Port selectivo / cherry-pick / manifests / tests.

## W10

Concentración sectorial debe quedar BINDING sí o sí.

Rama:
`deploy-prep/rc6-wave10-currentbase-20260909`

HEAD conocido:
`617244b23b54e4c227b1d76bc217460be4d78cd5`

Hacer live proof tras integrar:
- container policy BINDING;
- apertura bloqueada al límite;
- cierres no afectados.

## W18

Rama:
`deploy-prep/rc6-wave18-currentbase-20260909`

V4 run:
`34389298145` SUCCESS.

Usar package sin rollback automático.

## FAMILIAS

P0:
scraping/Contract Evidence debe completar TODAS las familias PPI.

Bloqueadores:
- Bonos/Letras/ON nominal units;
- Opciones contract;
- Futuros margin/contract;
- Cauciones términos/costo;
- ETF/Índices mapping/readiness.

No inventar contratos.

## OPCIONES

~382 AVAILABLE y 0 READY_PAPER en snapshot conocido.

Faltan:
- underlying;
- strike;
- expiry;
- CALL/PUT;
- multiplier/lot;
- provenance.

## CAUCIONES

10 AVAILABLE.
Debe existir evaluación live aunque termine HOLD.
No promover si faltan fees/derechos/términos.

## HISTÓRICOS PPI

Orden:
`contract → can_simulate → historical`

No descargar a fuerza instrumentos sin contrato.
W13 ya está materializada.
A3 sigue `ALIGNMENT_UNVERIFIED`.

## IOL

Sí sirve como histórico complementario.

Usar:
- read-only;
- backfill;
- reconciliación;
- provenance IOL.

No sustituye PPI Contract Evidence.

Antes de integrarlo completamente, corregir persistence multi-source:
actual PK `(symbol,date)` puede pisar provenance.
Objetivo conceptual `(symbol,date,source)`.

## DASHBOARD

Los defectos visuales principales ya quedaron corregidos:
- headers;
- titles;
- top/sticky nav;
- submenus;
- internal index;
- no duplicate Scalping;
- family activity;
- Risk expansion.

W17 live proof SUCCESS.

## LEGACY

Debe seguir:
- `ACTIVE_LEGACY_RUNTIME=0`
- `ENABLED_LEGACY_TIMERS=0`

## PRIMERAS ACCIONES EN ESTE NUEVO CHAT

Sin pedirme más contexto:
1. confirmar lectura completa;
2. refrescar GitHub/runtime;
3. semáforo actual;
4. lanzar en paralelo:
   - W12 latest live proof;
   - W10 integration/live proof;
   - W18 final integration;
   - IOL provenance/storage prep;
   - family readiness;
   - historical readiness;
5. checkpointar cada cierre;
6. crear integration candidate;
7. deploy serializado con full postflight.

## ESTILO

Español.
Directo.
Técnico.
Semáforo.
No promesas a futuro.
No repetir preguntas respondidas.
No llamar RED al motor si falla sólo Contract Evidence.
No llamar GREEN sin proof.

## FRASE DE CONTINUIDAD

Continúa desde RC6 live `9197aed...`, con W12 como P0 porque todavía no produce fresh Contract Evidence; usa el W12 current head `d7cb021...` o un sucesor verificado, mientras terminas de integrar W9–W18 e IOL histórico en paralelo, manteniendo PRODUCTION_PAPER y órdenes reales en cero.
