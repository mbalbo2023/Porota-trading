# RC6 — Diseño de GDELT en Shadow

## Estado

No existe todavía un adaptador GDELT dentro de Porota. El componente de
noticias actual es RSS y no debe presentarse como GDELT.

La integración será exclusivamente **read-only** y **SHADOW** en la primera
etapa: nunca tendrá autoridad para abrir, bloquear, dimensionar o cerrar una
posición PAPER.

## Objetivo

Agregar contexto de eventos/noticias globales, con especial foco en Argentina,
BYMA y emisores de acciones/CEDEARs operables. Debe complementar —no
reemplazar— la señal técnica, las velas, los históricos y el contexto BCRA.

## Contrato mínimo de datos

Cada observación debe guardar:

- consulta versionada y alcance temporal;
- fecha de publicación y fecha de recolección;
- URL, dominio, título, idioma y país fuente;
- identificador o hash para deduplicación;
- resultado de tono/volumen cuando esté disponible;
- identidad de instrumento sólo cuando el mapeo sea explícito;
- estado de calidad y motivo ante resultados incompletos.

No se debe inferir exposición por coincidencias parciales de ticker.

## Arquitectura

1. Un worker programado consulta la API DOC de GDELT con límites estrictos.
2. Persiste resultados idempotentes y una métrica agregada por ventana.
3. El adaptador del motor lee sólo la base local, nunca consulta red en rueda.
4. Para cada candidato BUY guarda un diagnóstico:
   `READY`, `INSUFFICIENT_DATA`, `STALE` o `UNAVAILABLE`.
5. El dashboard muestra la evidencia y deja explícito
   `decision_effect=OBSERVE_ONLY`.

## Límites iniciales

- Frecuencia: cada 30 minutos, con `max_instances=1`.
- Timeout de red corto y backoff exponencial.
- Ventana de consulta: últimas 24 horas; sin look-ahead.
- Máximo pequeño de resultados por consulta; sin descargar cuerpos completos.
- Retención comprimida de agregados y metadatos, no de páginas enteras.
- Sin token, cookies ni credenciales en el repositorio.

## Criterios para promoción futura

Antes de cualquier autoridad BINDING debe haber:

- historial suficiente de comparaciones contra resultados PAPER;
- evaluación point-in-time sin filtración temporal;
- falsos positivos/falsos negativos medidos;
- reglas explícitas de qué evento y qué instrumento se relacionan;
- aprobación humana versionada.

Hasta cumplirlos, GDELT seguirá siendo Shadow y no tendrá capacidad de veto.
