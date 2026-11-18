## Cambio y motivo

Describir el problema, el cambio y el comportamiento resultante.

## Ownership

Completar estos campos con el scope de esta PR y la declaración activa en
[el registro de ownership](https://github.com/mbalbo2023/Porota-trading/issues/980).
La lista de paths incluye archivos eliminados y ambos lados de un rename.

Workstream ID: [identificador]
Owner declaration URL: [URL del comentario activo]
Work branch: [rama head de esta PR]
Base SHA: [SHA completo actual de la base]
Scope paths: [paths exactos separados por comas]

## Reutilización obligatoria y destino del PR

Antes de escribir código: inventariar HEAD, ramas, PR previos abiertos/cerrados,
issues, parches locales, tests y workflows para este defecto. Enlazar la solución
parcial recuperada o justificar con evidencia por qué no sirve. No duplicar código
ni abrir otro PR por la misma solución.

Declarar owner y disposición del PR (`INTEGRAR`, `COMPLETAR`,
`RETENER_CON_CAUSA`, `SUPERSEDIDO_CON_REFERENCIA` o `CERRAR_CON_EVIDENCIA`),
siguiente acción y plazo. Ningún PR queda sin responsable ni se elimina por edad.

## Evidencia y revisión

Indicar qué se verificó, con su SHA y resultado, y qué sigue NO_VERIFICADO.
Explicar limitaciones y riesgos que afecten la revisión. La CI de main prueba
el plano de control; Predeploy certifica su paquete exacto y Promote registra
el resultado de runtime. Un cambio de documentación no requiere deploy.

Se mantienen PAPER/SHADOW ONLY, PRODUCTION_PAPER / SIMULATION, bloqueo de órdenes
reales y FIX-FORWARD. PPI Watch **no es intocable por regla del usuario**:
indicar expresamente si integra o no el scope y acreditar ownership y aislamiento
antes de mutar componentes. No imponer cuotas PPI supuestas. Los valores
observados de runtime requieren evidencia actual; esta plantilla no los certifica.
Mercado cerrado no veta deploy técnico 24×7, pero identidad contenedor/controlador,
manifest, PAPER/0 y health siguen bloqueantes. Current/Book frescos de la próxima
rueda: registrar instrumento, owner, plazo y Acción de QA; no promover a READY
por ausencia de datos. `NO_VERIFICADO` no es cierre ni licencia para inventar PASS.
