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

## Evidencia y revisión

Indicar qué se verificó, con su SHA y resultado, y qué sigue NO_VERIFICADO.
Explicar limitaciones y riesgos que afecten la revisión. La CI de main prueba
el plano de control; Predeploy certifica su paquete exacto y Promote registra
el resultado de runtime. Un cambio de documentación no requiere deploy.

Se mantienen PAPER/SHADOW ONLY, PRODUCTION_PAPER / SIMULATION, bloqueo de órdenes
reales, PPI Watch fuera del scope y FIX-FORWARD. Los valores observados de runtime
requieren evidencia actual; esta plantilla no los certifica.
