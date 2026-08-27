# Modos de operación — Porota Trading v16.3.5

## Ciclo horario y sincronización de instrumentos

En `PRODUCTION_PAPER` el proceso tiene tres fases explícitas. Fuera de rueda
queda `WAITING_MARKET` y no evalúa señales. En los quince minutos anteriores a
la apertura queda `READY_PREOPEN`: puede autenticar y sincronizar catálogo e
históricos, pero no crea decisiones ni operaciones paper. Solamente con la
rueda BYMA abierta consulta puntas y entrega cotizaciones al simulador.

La búsqueda productiva de PPI siempre envía `Ticker` y `Name` no vacíos. El
universo curado del proyecto se valida candidato por candidato; los candidatos
de contado confirmados pueden incorporarse al conjunto paper hasta el tope
configurado. Opciones y futuros quedan visibles como contexto, pero no se
simulan mientras falten vencimiento, multiplicador o margen atribuible.

Autenticación, catálogo, históricos y market data se registran por separado:
una falla de un dato posterior al login nunca vuelve a declarar falsamente que
la autenticación falló.
El dashboard corre siempre en un contenedor separado y no recibe credenciales
de PPI. El selector impide que dos motores queden activos al mismo tiempo.

## Dashboard operativo

Todas las páginas comparten un único menú superior. Incluye Panel, Motor de
trading, Salud, Históricos, Aprendizaje, Información financiera, Reportes,
Telegram, SRE, Logs y Configuración. Se eliminó `Volver`; cada página termina
con el enlace accesible `Ir al principio` y existe un solo refresco visual.

- **Motor de trading:** cada operación paper se despliega individualmente y
  muestra señal, variables, costos, fills simulados, eventos y veredicto.
- **IA y portones:** Gemini es un portón crítico, pero no el último control.
  La estrategia propone; Gemini aprueba o veta; luego capital, exposición y
  liquidez pueden bloquear correctamente la apertura. Toda la secuencia queda
  persistida y explicada. Si el modelo o su contrato JSON fallan, no se abre ni
  siquiera una posición simulada. Cada veredicto queda persistido. El modelo
  no queda fijado a un nombre heredado: se consulta el inventario visible para
  la clave, se filtran los modelos de texto con `generateContent` y se usa el
  primero vigente. Si no hay uno compatible, el portón permanece cerrado.
- **Salud:** inventaría las APIs, modo/uso, último reporte, último éxito y
  próximo chequeo. La autenticación Sandbox se prueba una sola vez durante la
  instalación, sin consultar cuenta ni enviar órdenes.
- **Históricos:** muestra último intento/éxito por fuente y fecha del catálogo.
- **Sincronización manual:** el botón del dashboard encola una orden local. El
  observador aislado realiza un solo login PPI de solo lectura y descarga datos;
  el dashboard no recibe secretos y la lista blanca bloquea cuentas y órdenes.
- **Sincronización diaria:** al primer login de mercado de cada día se actualizan
  catálogo e históricos; reiniciar el observador no duplica esa bajada.

OPENBYMADATA es una web pública oficial. Las APIs oficiales de BYMA requieren
alta o contratación, por lo que v16.3.4 no intenta utilizar endpoints ocultos.
Hasta contar con ese acceso, catálogo e históricos se obtienen de PPI Producción
bajo la barrera de solo lectura. Cada instrumento devuelto por PPI queda
inventariado. El lote inicial de 20 rota sobre todo el universo; SRE registra
duración, errores y límite recomendado. Los históricos se descargan en lotes
incrementales de hasta 40 hasta cubrir todos los instrumentos, no sólo los 20
del ciclo.

## Operación 24x7, SRE y reportes

El dashboard sigue activo con la rueda cerrada. El observador conserva el
heartbeat y ejecuta servicios no operativos: noticias, indicadores oficiales
BCRA/INDEC, SRE, backup diario con restore test, informes y Telegram de cierre.
El menú SRE separa resumen, base, backups, infraestructura y performance.

Reportes conserva PDF diarios y mensuales y un paquete JSON intensivo para IA
con operaciones, variables, secuencia de portones, noticias y lecciones. Los
semanales son transitorios: al crear el mensual integrado se eliminan los de
ese mes; los diarios permanecen disponibles.

## Patrimonio en simulación

El capital inicial es **$1.000.000 ARS ficticios**. No se consulta ni se copia
el saldo real de la cuenta. La política inicial es: riesgo máximo 0,5% por
operación, tope 25% por posición, hasta tres posiciones y exposición total
máxima 60%. La cantidad final es el menor límite entre riesgo hasta el stop,
efectivo paper, tope individual, tope total y liquidez visible.

Producción real no puede reutilizar ese capital: si se habilita en una versión
futura deberá conciliar el patrimonio real de PPI y bloquear el dimensionamiento
si ese dato no está disponible.

| Modo | Datos | Ejecución | Telegram |
|---|---|---|---|
| DETENIDO | Ninguna API | Ninguna | Avisa la detención |
| SANDBOX | PPI Sandbox | Órdenes de prueba | Informa SANDBOX |
| SIMULACIÓN PRODUCTIVA | PPI Producción, solo mercado | Compras/ventas locales simuladas | Informa SIMULACIÓN |
| PRODUCCIÓN REAL | PPI Producción | Dinero real | Bloqueado en v16.3.5 |

Comandos del administrador instalado:

```bash
sudo porota-mode status
sudo porota-mode simulation
sudo porota-mode sandbox
sudo porota-mode stop
```

`production` permanece deliberadamente bloqueado. Habilitarlo requiere otra
versión auditada, una marca local independiente y confirmación explícita de
dinero real. El cambio de modo se guarda en `data/operation_mode.json`, se
muestra en todas las páginas y en Salud, se informa por Telegram y deja un
resumen corto en el portapapeles de la terminal.
