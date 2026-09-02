# IA intradía — retirada de la operatoria de Porota Trading

Estado canónico HF6 y siguientes salvo decisión explícita del propietario.

## Política

La IA intradía no participa del proceso de decisión, sizing, aprobación, veto, entrada, salida ni ejecución PAPER.

- `PAPER_AI_GATE_MODE=OFF`.
- El motor intradiario es Python determinístico y versionado.
- Gemini no es dependencia de disponibilidad del ciclo de trading.
- Un error, latencia o indisponibilidad de Gemini no debe bloquear una decisión Python válida.
- Ningún resultado de IA puede enviar una orden, aprobar un fill ni mutar parámetros automáticamente.

## Archivos históricos

Los módulos relacionados con IA pueden permanecer temporalmente en repositorio/imagen para compatibilidad, trazabilidad histórica o análisis offline. Su presencia física no implica uso operativo.

No se eliminan como parte del patch HF6 v2 para evitar regresiones no relacionadas. Una limpieza futura deberá demostrar primero que no son importados por ningún runtime activo ni requeridos por reportes históricos.

## Uso permitido

Sólo se admite, si continúa autorizado, análisis offline/post-rueda de evidencia o generación de propuestas no vinculantes. Esas propuestas requieren revisión humana y una promoción versionada independiente.

## Reactivación

Reactivar IA intradía exige una decisión explícita del propietario, nueva versión de estrategia, replay/test fuera de muestra y revisión de seguridad. No puede producirse por variable heredada, fallback ni autodetección.
