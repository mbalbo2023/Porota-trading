# RC6 #624 — SHADOW COLD y capacidad, candidato sin promoción

**19/10/2026, workstream** `WS-RC6-624-DUALTRACK-INTEGRATOR-20261019`.
Ownership pareado: #471/6272519341 + #473/6272521663; SRE exacto
#471/6272596904 + #473/6272598562. Fuente productiva de partida
`d52e45fe85ed184a76d35120ca543f5d63f38b2c`, árbol
`59d0fa6efc44eb22a10308bb90912d4d69642db8`; `main` histórico
`5a87538d3816601a4fddf4aaafc975b4ebc674d7`. Revalidar antes de toda
promoción. Este documento no autoriza Deploy ni cuarentena.

## A. Hechos y matriz causal

| Falla/condición | Evidencia y límite | FIX/GUARD de candidato | Regresión/puerta |
| --- | --- | --- | --- |
| Primer COLD2 de `7dc99ccd8f7c47849ca3705ae1cb2c49` terminó `FAILED/ValueError` | Recibo original sólo tiene clase, sin código/fase; pack 211783290 B SHA `d19b7e9062327a4f9d013b57cff9f2d1824f9dba386d9756bc0e2a0b3240cf0c`, manifest digest `7657b124c222993f9b34471626d0c740508ee783e7fcd8ce07c85ad9495f23f3`; fallo 27,724 s antes del deadline. Replay de bytes originales Actions 39254545397 terminó COMPLETE en 3,354/2,547 s fuera del host. Subcausa original **NO_IDENTIFICABLE** con el recibo disponible; no inferir RAM, disco, codec o timeout. | Selección revisada de PR644: primer `FAILED` futuro conserva código `SHADOW_` y fase acotados, nunca texto arbitrario ni reescritura del primer recibo. | Guard original PR644, Predeploy 39259624680 sólo certifica `0b109b0b…`; se requiere certificado nuevo para todo cambio. |
| `ensure_idle` bloquea correctamente todo nuevo corte al encontrar recibo+queue FAILED; `CURRENT` del cut `af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9` permanece PENDING/revocado | Deploy 39185545107: Native2 90/90 rechazos, cero generaciones; Strict10 y estrés host no ejecutados. | Script de cuarentena exact-ID **deshabilitado en CLI** conserva mismo pack/inodo vía rename en mismo FS, copia atestada de manifest/primer recibo/queue, journal/fsync y `QUARANTINED_FAILED`; deja viejo cut revocado. Sólo una nueva captura auténtica con audit COMPLETE puede recuperar SHADOW. | Tests de crash en cada frontera, bytes/hash/inodo originales, idempotencia, archivo ambiguo, alias, cambio de CURRENT/status y mercado cambiante. Sin quiescence CLOSED real no se ejecuta. |
| Cualquier llamada futura a cuarentena con admisión incompleta podía confiar en un callback que omitiera modo/rutas, inflight, IDs o recursos | Revisión adversaria del procedimiento no cableado; no hay prueba de ejecución productiva. | Candidato exige `PRODUCTION_PAPER`, entero 0, rutas `NOT_CALLED` y bloqueadas, cero inflight/posiciones, critical healthy, CLOSED, lock, PPI sin acceso, dos IDs exactos fijados externamente; revalida antes de cada mutación. Reserva RAM >128MiB, disco >=2GiB+1MiB e inodos >=16. | Nuevos tests negativos de cada campo y tres reservas; ningún estado `COMPLETE` fabricado. Validación del callback real/IDs y sesión tras admisión sigue pendiente de workflow futuro autorizado. |

El flujo durable sigue `CAPTURE → SEALED → PENDING → COMPLETE/FAILED →
QUARANTINED_FAILED`. La cuarentena conserva evidencia, no certifica la causa
histórica ni convierte FAILED en COMPLETE. El script no tiene opción `--apply`;
su único CLI es `--plan` de lectura. La primera prueba de un nuevo corte será
postquarantine, CLOSED, bajo dueño global legítimo y autorización específica.

## B. Capacidad: reconciliación parcial, no orden de borrado

Artefacto de inventario READ_ONLY run 39258140820 / 12189270586 (ZIP
SHA256 `f98bf2d399ba43dddfdf009cc45ce3dff75d0e9ea70864ad3afa0ab97c4b0c64`):

| Clase física (corte 13:12 UTC) | Bytes/estado | Referencia/decisión |
| --- | ---: | --- |
| Docker images | 4,723 GB declarados, 2/2 ACTIVE | IDs `2a12960d…` y `687af03e…` sostienen los tres contenedores; 0 reclamable. Tags múltiples del mismo ID no se suman. |
| Docker build cache | 1,402 GB, 8/8 ACTIVE | 0 reclamable sin identificar refs/leases. |
| `/var/lib/containerd` | 6305083392 B `du -sx` | Solapa imágenes/caché de Docker; **no sumar** 4,723+1,402 otra vez. |
| `/opt/porota-backups` | 1353347072 B `du -sx` | General3/paper3/history3, keep3 declarativo: RETAINED. |
| `/home` | 346963968 B `du -sx` | Uso exacto interno y referencias NO_VERIFICADOS. |
| `data/deploy` | 113479680 B `du -sx` | Incluye archivo forense/evidencia y recibos; nada probado huérfano. |
| `/var/log/journal` | 16785408 B `du -sx` | Debajo del cap de política 512MiB; no limpiar. |
| `/opt/porota-trading/data` | `UNAVAILABLE` por timeout | Principal hueco del mapa físico; no atribuir bytes faltantes ni sumar por intuición. |

La muestra de 13:12 UTC dio `free=4477886464 B`; la sonda OPEN de
16:27:59 UTC/13:27:59 ART dio `4440797184 B`, frente a piso del **artefacto
anterior** `4923227373 B`: déficit `482430189 B` en esa muestra. No existe
todavía umbral calculado para el futuro candidato. Meta orientativa 8 GB
decimales = 8000000000 B (7,451 GiB); frente a esa muestra faltaban
3559202816 B, sin afirmar que exista espacio elegible. El FREE dinámico
puede variar. Historia de recuperaciones previas: imagen antigua sin refs
~2225913856 B, apt/journal/backup excepcional según #624; esa imagen ya no
aparece como huérfana. No repetir la misma limpieza.

El PR diagnóstico #645, **DRAFT/NO_MERGE**, usa un workflow existente en rama
aislada para mapa de metadatos de una sola pasada bajo OPEN/PAPER/IDs/health,
sin `du` recursivo ni borrado. El primer run 39290414283 rechazó el uso
erróneo de admisión CLOSED durante OPEN; fix-forward del gate en SHA
`c0fdf61e598b2cb16cdb75a8044977d37a58a568` mantuvo la lectura
acotada. El siguiente resultado y cobertura deben añadirse sin reetiquetar
los RED. Si RAM/PSI o una fase crítica rechazan la sonda, **STOP**; el
inventario faltante se hace bajo CLOSED auténtico con cuotas y referencias.

El run posterior **39290650947** terminó FAILED en `snapshot()` antes de
inventariar: la aserción de `MemAvailable > 134217728 B` rechazó el host.
La sonda no guardó el número exacto, por lo que el valor se clasifica
`NO_VERIFICADO`; el incumplimiento del guard sí está demostrado por log del
job 118190110760. No se repitió la sonda para perseguir GREEN. Cero bytes
recuperados por #645; ninguna limpieza puede admitirse con este estado.

## Puertas y estado

PAPER/SHADOW ONLY; PPI Watch intocado, real_orders_sent=0 según último
artefacto pasivo citado, no una validación continua. Código candidato
`EN_GITHUB` sólo tras verificar SHA/tree. Exigir suite descubierta=ejecutada,
JUnit/JSON 0 FAIL/ERROR/SKIP/XFAIL, 405 virtual/8 grupos OFFHOST, OCI/ELF,
manifest y hashes del **nuevo** SHA antes de `ARTEFACTO_VALIDADO`. No merge,
Deploy, stress host ni cuarentena por esta misión. Native2+Strict10,
405 virtual/8 grupos EN DROPLET, restauración de mismos IDs, nuevo 2+10 y
perfil independiente son **NO_EJECUTADOS** para el nuevo candidato. Estado
de runtime: **NO_VALIDADO_RUNTIME/FAIL_CLOSED**, no READY ni fills ficticios.

Si capacidad dinámica y riesgo no se acreditan, alternativas sólo
`PROPUESTO`: reducción de huella de nueva imagen, housekeeping gobernado,
retención offhost verificable o ampliación autorizada; sin ejecutar resize,
movimiento, compras ni alterar keep3. La fecha de CLOSED se prueba por DB y
estado de servicios, nunca por reloj.
