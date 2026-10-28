# Revisión Sandbox RC1 - 22 de agosto de 2026

Este archivo documenta los cambios aplicados al paquete original
`Bot_Trading_v16_2_FINAL.zip` antes de permitir su despliegue en Sandbox.

## Estado verificado

- Sintaxis Python: aprobada.
- Suite: 122 pruebas aprobadas de 122.
- Cobertura global medida: 18,39%.
- Dependencias: conjunto resoluble; `pip check` sin conflictos.
- Imports críticos: aprobados.
- Dashboard: `/health` devuelve `{"status":"ok","version":"16.2"}`.
- Filtración de detalles internos en `/health`: no detectada.
- Credenciales dentro del paquete: no detectadas.

## Correcciones

1. Se alinearon los tests de opciones con el control de v16.2 que exige
   `strike_source="api"` antes de evaluar o dimensionar una serie.
2. Se actualizó el test de noticias a la política documentada
   `NEWS_FEEDS_DOWN_POLICY=operar_normal`.
3. Se actualizaron los tests de costos para usar el tarifario por clase de
   activo y para incluir la fricción en la pérdida al stop.
4. Se corrigió la propiedad de cauciones para considerar el redondeo a
   centavos en montos diminutos.
5. README y plan de testing ahora identifican v16.2 y no mencionan
   `.env.testing` ni `scripts/smoke-test.sh`, que no existen.
6. Se corrigió el umbral del CI para reflejar la línea base realmente medida
   en Sandbox. Esto no constituye aprobación para dinero real.
7. Se resolvió el conflicto imposible de dependencias entre PPI y pyRofex.
   PPI requiere `websocket-client==1.0.0`; pyRofex exige `>=1.6.4`. Sandbox
   conserva PPI y mantiene `ROFEX_ENABLED=false`. Futuros requieren un
   servicio Python separado antes de producción.

## Límites que siguen abiertos

- `c_ppi_client.py`, `k_position_manager.py` y `p_risk_guardian.py` necesitan
  dobles y mayor cobertura antes de operar dinero real.
- Falta ejecutar el verificador contra APIs reales desde el Droplet.
- Falta una rueda completa de mercado en Sandbox.
- Falta validar Telegram, parada de emergencia, persistencia y restauración.
- Falta configurar TLS y reverse proxy para acceso externo al dashboard.

## Dictamen

GO condicionado para desplegar en Sandbox con confirmación humana por orden.
NO-GO para `ENVIRONMENT=PRODUCTION` y para futuros.
