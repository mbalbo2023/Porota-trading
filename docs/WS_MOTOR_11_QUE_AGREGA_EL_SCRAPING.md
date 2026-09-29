# WS-MOTOR-11 — QUÉ AGREGA EL SCRAPING

## Conclusión

WS11 no ejecutó scraping nuevo. Reutilizó las capturas autenticadas, sanitizadas
y read-only de WS10. El scraping/DOM no es el camino crítico general y sólo se
conserva como fallback por campo indispensable. `scraping_fallback_allowed`
rechaza DOM si una fuente no-DOM ya resolvió el campo o si el campo no es
requerido por el perfil PAPER.

## Valor indispensable ya demostrado

| Testigo/campo | Por qué no bastó MCP/oficial | Necesidad PAPER | Origen heredado | Cambio/captura | Fallback y guard |
|---|---|---|---|---|---|
| GD30 `quantity_min`, `quantity_step` | IOL units-per-lot no es término de orden; simulador no prueba step | sizing y rechazo de orden inválida | formulario autenticado PPI de GD30, antes de confirmación | contractual; recapturar ante hash/cambio | fail-closed si selector/payload desaparece; no copiar a AL30 |
| GD30 `price_quote_unit=100 VN` | quote/simulador deben reconciliar unidad; una barra no la prueba | conversión precio->cash | ticket/quote autenticado PPI | contractual | contraste IOL/oficial; conflicto bloquea |
| GFGC6000OC lote/multiplicador y cantidad de contrato | chain IOL da serie/strike/expiry, no todos los términos del ticket PPI | prima, exposición y sizing | formulario autenticado PPI | contractual | identidad exacta; no copiar a otra serie |
| Adcap Ahorro Pesos clase observada: mínimo, step, cutoff, rescate y unidad NAV | inventario MCP sólo lista fondo/moneda/operable | simulación de suscripción/rescate | ficha autenticada PPI, sin aceptar DDJJ | términos/NAV con frecuencias distintas | clase exacta obligatoria; NAV exige fecha; executor sigue bloqueado |
| DLR/DIC26 binding PPI | A3 define contrato pero no qué especie exacta ofrece PPI | vincular contrato oficial al broker primario | ficha/búsqueda autenticada PPI | identidad contractual | sólo serie exacta; no extrapolar a ENE27 |
| cauciones: estado de sesión observado | MCP tasa no prueba profundidad/aceptación actual | gate dinámico | página autenticada PPI after-hours | dinámica, minutos | sin provider timestamp/depth no habilita PAPER |

## Qué no agrega

- No aporta autoridad superior a PPI structured/XHR.
- No convierte un timestamp de captura en timestamp de mercado.
- No prueba profundidad ejecutable de cauciones.
- No reemplaza especificaciones BYMA/A3, margen de clearing o términos de la
  gestora.
- No crea identidad PPI a partir de una especie IOL.
- No permite declarar READY una familia por haber visto una especie.

## Política operativa

1. PPI structured/catalog.
2. IOL structured MCP y fuentes oficiales.
3. XHR/JSON autenticado.
4. DOM PPI sólo para un campo requerido aún no resuelto.
5. DOM IOL sólo como último complemento.
6. Cambio de HTML/selector => evidencia ausente y fail-closed, nunca valor
   heredado silenciosamente.

No se guardan cookies, credenciales, OTP ni HTML de cuenta. No se llegó a una
pantalla de confirmación ni se invocó un validador ligado a cuenta real.
