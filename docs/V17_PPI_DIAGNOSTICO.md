# Diagnóstico público PPI de una sola ejecución

## Instalación confirmada por el operador

El operador aportó inspección Docker: `porota_trading_bot` usa imagen 16.3.3;
`porota_production_observer`, 16.3.5. Ambos tienen `Running=false` y
`RestartPolicy=no`, usuario `botuser`. El observador monta
`/opt/porota-trading/.secrets/ppi_production.json` en
`/run/secrets/ppi_production.json`, sólo lectura. Esto es evidencia aportada,
no inspección SSH independiente. La imagen del bot anterior no se reutiliza.

## Ejecución accesible

1. Transferir por SFTP `porota_ppi_diagnostico_v17.zip` a `/tmp/` del servidor.
   No descomprimir, cambiar la rama, instalar dependencias o desplegar v17.
2. Ejecutar una sola vez:

```bash
sudo -n python3 /tmp/porota_ppi_diagnostico_v17.zip --host
```

3. Pegar el JSON de salida. Se imprime y se envía al portapapeles mediante
   OSC52 si Termius lo admite. No repetir hasta revisar el resultado.

`sudo -n` no solicita contraseña; si no está autorizado se detiene. No se
agrega el usuario al grupo Docker ni se cambian permisos del socket/secreto.
El ZIP original se conserva. Una copia temporal del paquete se hace legible
para `botuser`, sin cambiar permisos del archivo subido ni copiar credenciales.

## Alcance exacto

- Host usa sólo Python estándar. Revalida estados, imagen, usuario y montaje.
  Una instalación distinta o un motor activo/con reinicio habilitado detiene
  el procedimiento antes de crear el diagnóstico. La inspección no lee Env.
- Resuelve el ID inmutable de la imagen del observador detenido; sin pull ni
  build. Crea `porota_v17_ppi_probe` con entrypoint Python, sin healthcheck,
  sin montar `.env`, datos/SQLite, cachés del motor ni puertos del dashboard.
- Conserva ejecución como `botuser`, raíz de contenedor sólo lectura y las
  restricciones existentes. Sólo monta el ZIP y el secreto autorizado, ambos
  read-only. No importa el motor, dotenv ni Telegram.
- Un intento de login por ejecución, siete configuraciones, hasta cuatro
  búsquedas y un candidato nuevo por filtro. Cada candidato puede recibir una
  lectura de book y una de current; máximo previsto veinte solicitudes HTTP.
  Límite defensivo de veinticuatro y tiempo total de ciento veinte segundos.
- Filtros CAUCION/PESOS/DOLAR exploran CAUCIONES; ALUA es control de ACCIONES
  observado en el catálogo aportado. Se consultan sólo familias/mercado/plazos
  enumerados por PPI. Un filtro no es un ticker confirmado; para book/current
  se usa exclusivamente una identidad completa efectivamente devuelta.
- Los plazos consultados se etiquetan como argumentos de consulta, no términos
  contractuales certificados de cada especie. Vacío no significa familia
  inhabilitada; una búsqueda parcial no pretende cubrir todos los instrumentos.
- Un HTTP distinto de 200, timeout, forma inesperada o problema de credenciales
  detiene la secuencia. No se imprime el cuerpo del error, credenciales o tokens;
  no hay segundo login, refresh, reintento o fuente alternativa automática.
- No existen consultas de cuentas, saldos, posiciones, presupuestos, órdenes,
  confirmaciones o cancelaciones. La barrera HTTP existente se empaqueta sin
  modificarla. El rechazo se intercepta antes del refresh interno del SDK.
- Hasta cinco filas/niveles por muestra, campos públicos seleccionados y fechas
  del proveedor sin reinterpretación. No convierte price a TNA, quantity a
  capital, ni bids/offers en lado colocador. No infiere base anual/costos.
- `HOLD_UNVERIFIED_TERMS`, `data_certified=False`, `promotion_allowed=False`.
  El diagnóstico no habilita cauciones ni completa el adaptador financiero.
- El host espera hasta ciento cincuenta segundos y elimina exclusivamente el
  ID del contenedor que acaba de crear, también ante timeout. Un nombre ocupado
  no se reutiliza ni borra. Si falla la limpieza, se informa STOPPED y requiere
  revisión. No modifica ni inicia los contenedores originales.

No impide un arranque manual ajeno que ocurra después de la inspección inicial.
Se autentica realmente en PPI cuando el operador ejecuta el paquete; los tokens
se descartan al terminar, pero no se presume revocación inmediata en el broker.

## Construcción y evidencia

`scripts/build_v17_ppi_probe.py` genera un ZIP ejecutable reproducible con
exactamente `__main__.py`, `bd_ppi_readonly_guard.py` y `MANIFEST.json`. Incluye
huellas de las fuentes, no credenciales ni archivos del sistema. No sobrescribe
un ZIP existente. El comando del usuario no lleva código largo ni hashes.

Pruebas con SDK instalado y transporte HTTP sustituido, Docker sustituido,
timeouts, limpieza, aislamiento y ZIP/CLI. No se ejecutó contra PPI ni Docker
del servidor desde el entorno de desarrollo. CI general SANDBOX no valida el
login productivo ni el comportamiento comercial de los instrumentos.

Fuentes: [REST oficial de PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/),
[Python oficial de PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/)
y código del SDK instalado. Los ejemplos genéricos documentan endpoints y
formas; no certifican términos de caución o tarifas aplicables a esta cuenta.
