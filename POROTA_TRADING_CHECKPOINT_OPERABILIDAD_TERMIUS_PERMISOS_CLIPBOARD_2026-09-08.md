# POROTA TRADING — CHECKPOINT OPERABILIDAD TERMIUS / PERMISOS / CLIPBOARD — 2026-09-08

## Regla canónica de interacción con el operador

El operador usa Android + Termius + control por voz. Por lo tanto, cualquier script/comando entregado para ejecución manual en el Droplet debe cumplir obligatoriamente:

1. **No depender del bit ejecutable** del archivo. La forma preferida de ejecución es `bash /ruta/script.sh` o `sudo -n bash /ruta/script.sh` según corresponda. No asumir que `chmod +x` funcionará ni que el archivo conservará permisos al descargarse/subirse.
2. **Un solo bloque de comandos** siempre que sea posible.
3. **Scripts en formato `.sh`** salvo pedido expreso distinto.
4. **Sin prompts interactivos**; usar `sudo -n` cuando sea posible.
5. **Salida compacta y útil**, evitando desbordar portapapeles/Termius/Android.
6. **Toda salida operativa debe quedar guardada en un archivo de texto** con ruta explícita.
7. **La salida debe intentar copiarse automáticamente al portapapeles del cliente** mediante OSC52 cuando el terminal lo soporte. Si no lo soporta, el script debe mostrar claramente la ruta del archivo de salida y una salida final compacta fácil de seleccionar/copiar.
8. Nunca considerar una prueba ejecutada hasta que el operador devuelva la salida o exista evidencia remota verificable.
9. Los scripts de diagnóstico deben preservar invariantes: no modificar `.env`, secretos, runtime ni estado salvo que el usuario autorice explícitamente el cambio.

## Motivo

Este checkpoint se crea porque ya hubo múltiples fallas operativas por:
- archivos descargados sin permiso de ejecución (`Permission denied`);
- comandos que producían salida pero no la dejaban preparada para copiar/pegar de vuelta a ChatGPT.

Estos fallos deben tratarse como fallos de diseño de la interacción, no como errores del operador.

## Aplicación inmediata

El diagnóstico PPI productivo read-only debe rehacerse bajo estas reglas:
- invocación por `bash`/`sudo -n bash`;
- salida persistida;
- intento de OSC52;
- sin modificación de credenciales;
- sin rutas de órdenes;
- `real_orders_sent=0` preservado.
