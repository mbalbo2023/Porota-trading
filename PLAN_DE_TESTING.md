# Plan de Testing — Porota Trading v16.2

**Para:** el equipo de testing
**Objetivo:** que el sistema llegue a producción con la menor cantidad posible de errores que cuesten dinero.

---

## 0. La premisa: acá no todos los bugs valen lo mismo

En una aplicación común, un bug es un bug. En un sistema que opera con dinero, hay una jerarquía muy clara y conviene tenerla presente antes de escribir el primer test, porque determina en qué orden se prueba:

| Clase | Ejemplo | Costo |
|---|---|---|
| **Clase A — pierde plata en silencio** | Un costo mal calculado, un tamaño de posición mal dimensionado, un stop-loss que no se dispara | Altísimo. No falla, no tira error, no aparece en ningún log. Solo se nota en el resumen de cuenta, meses después. |
| **Clase B — deja de operar** | El bot se abstiene cuando debería operar | Alto pero invisible. Una operación que no se hizo no deja rastro. |
| **Clase C — se cae ruidosamente** | Excepción no capturada, contenedor que no levanta | Molesto, pero el más barato: se ve enseguida y se arregla. |

**La consecuencia práctica:** el esfuerzo de testing se concentra en la Clase A, aunque sea la menos vistosa. La v16.0 ya encontró uno de esos —el filtro de rentabilidad descontaba cien veces menos costo del real, sin fallar nunca— y lo encontró un test unitario, no una prueba manual.

---

## 1. Cómo arrancaría yo, en orden

### Paso 1 — Correr lo que ya existe y entender por qué existe

```bash
pytest -v
```

122 tests. Antes de escribir uno nuevo, leerlos: cada uno tiene un comentario explicando **qué propiedad fija y por qué importa**. Ese formato es el que hay que replicar.

### Paso 2 — Medir qué no está cubierto

```bash
pip install pytest-cov
pytest --cov=. --cov-report=html
# abrir htmlcov/index.html
```

**Advertencia de método:** la cobertura sirve para **encontrar caminos sin probar**, no para perseguir un porcentaje. Un 90% de cobertura con tests que no verifican nada es peor que un 40% con tests que fijan propiedades reales, porque da una falsa sensación de seguridad.

Lo que hay que mirar en el reporte: qué **funciones que tocan dinero** están en rojo.

### Paso 3 — Cubrir la Clase A primero

Prioridad, en este orden exacto:

1. `d_economics.py` — costos, piso, dimensionamiento **(ya cubierto: 16 tests)**
2. `ai_derivatives_engine.py` — pérdida máxima de derivados **(ya cubierto: 13 tests)**
3. `k_position_manager.py` — apertura, stop-loss, take-profit, cálculo de resultado **(PENDIENTE — máxima prioridad)**
4. `c_ppi_client.py` — armado de órdenes, mapeo de parámetros por tipo de instrumento **(PENDIENTE)**
5. `p_risk_guardian.py` — límites de pérdida y drawdown **(PENDIENTE)**
6. `e_technical_engine.py` — indicadores **(PENDIENTE, menor prioridad: un indicador mal calculado produce una mala señal, no una pérdida contable)**

### Paso 4 — Recién entonces, integración y aceptación

---

## 2. Cómo escribir un test unitario acá

La receta que se siguió para los existentes:

| Paso | Qué hacer | Ejemplo real del proyecto |
|---|---|---|
| 1 | Elegir una **propiedad** que tenga que ser cierta siempre, no un caso puntual | «El costo en porcentaje y el costo en fracción siempre difieren en un factor de 100» |
| 2 | Nombrar el test como una frase que describa esa propiedad | `test_una_opcion_lanzada_nunca_es_operable` |
| 3 | Preparar solo los datos mínimos | Un ticker de opción y una prima. Nada más. |
| 4 | Ejecutar una sola función | `deriv.assess_option(spec, 180.0, 7100.0, side="SHORT")` |
| 5 | Afirmar el resultado **y el motivo** | Verificar el código `CAPACIDAD_PERDIDA_NO_ACOTADA`, no solo que `capable` sea `False` |
| 6 | Comentar **por qué importa** esa propiedad | «La pérdida no tiene cota, la división no tiene resultado» |
| 7 | Verlo **fallar** antes de que pase | Romper a propósito la función y confirmar que el test se pone rojo |

El paso 7 no es opcional. **Un test que nunca falló no prueba nada**: puede estar verificando aire y nadie se entera hasta que hace falta.

### Ejemplo completo

```python
def test_el_tamano_nunca_supera_el_capital_disponible():
    """Un stop muy cercano permitiría comprar más de lo que se puede pagar:
    el tope por capital tiene que ganarle al tope por riesgo.

    Por qué importa: si esta propiedad se rompe, el bróker rechaza la orden
    (mejor caso) o la ejecuta y queda un descubierto (peor caso)."""
    qty = economics.calculate_position_size(
        available_capital_ars=10_000, entry_price=100.0,
        stop_loss_price=99.9, risk_pct=1.0)
    assert qty * 100.0 <= 10_000
```

---

## 3. Herramientas — todas gratuitas

### Base

| Herramienta | Para qué | Instalación y uso |
|---|---|---|
| **pytest** | Motor de tests. Ya configurado en `pytest.ini`. | `pip install pytest` → `pytest` |
| **pytest-cov** | Qué líneas no toca ningún test. | `pytest --cov=. --cov-report=html` |
| **Ruff** | Imports rotos, variables sin usar, errores de estilo. Corre en segundos. | `ruff check .` |

### Las que más valor aportan en este proyecto

| Herramienta | Por qué acá específicamente |
|---|---|
| **Hypothesis** | Genera cientos de entradas al azar buscando la que rompe. La lógica de costos y dimensionamiento es determinista y numérica: es el caso ideal. Encuentra los extremos que a una persona no se le ocurren (capital cero, precio negativo, stop igual a la entrada). |
| **freezegun** | Congela el reloj. Imprescindible acá: hay lógica de vencimientos de opciones, ventanas horarias de despliegue, cierre de rueda y desfasajes de publicación. Sin congelar el tiempo esos tests fallan en días distintos. |
| **responses** / **respx** | Simula respuestas HTTP sin salir a la red. Permite probar qué hace el sistema cuando el bróker devuelve un 429, un 500 o un JSON con un campo renombrado — escenarios que en la vida real no se pueden provocar a voluntad. |
| **mutmut** | Testing de mutación: rompe el código a propósito y ve si algún test lo detecta. Es la única forma objetiva de responder «¿mis tests sirven?». Usarlo solo sobre `d_economics.py` y `ai_derivatives_engine.py`; sobre todo el proyecto tarda demasiado. |
| **Bandit** | Detecta patrones inseguros: credenciales embebidas, `eval`, verificación SSL desactivada. | 
| **pip-audit** | Vulnerabilidades conocidas en las dependencias. |

### Instalación completa

```bash
pip install pytest pytest-cov hypothesis freezegun responses mutmut bandit pip-audit ruff
```

### Ejemplo con Hypothesis

```python
from hypothesis import given, strategies as st

@given(
    capital=st.floats(min_value=1_000, max_value=100_000_000),
    precio=st.floats(min_value=0.01, max_value=1_000_000),
    riesgo=st.floats(min_value=0.1, max_value=5.0),
)
def test_el_tamano_nunca_arriesga_mas_del_limite(capital, precio, riesgo):
    """Con CUALQUIER combinación válida, la pérdida al stop nunca supera
    el porcentaje configurado. Hypothesis prueba cientos de combinaciones
    y, si encuentra una que falla, la reduce al caso mínimo que la rompe."""
    stop = precio * 0.9
    qty = economics.calculate_position_size(capital, precio, stop, riesgo)
    perdida_maxima = qty * (precio - stop)
    assert perdida_maxima <= capital * (riesgo / 100) + precio
```

### Ejemplo con freezegun

```python
from freezegun import freeze_time

@freeze_time("2026-08-20 21:00:00")
def test_la_ventana_de_despliegue_esta_abierta_a_las_21():
    assert sre.condiciones_de_aplicacion()["puede_aplicar"] is not None

@freeze_time("2026-08-20 09:00:00")
def test_a_las_9_de_la_manana_no_se_despliega():
    cond = sre.condiciones_de_aplicacion()
    assert any("ventana" in b for b in cond["bloqueos"])
```

---

## 4. Generar tests con IA — guía paso a paso

Esta sección está escrita para que la sigas de arriba abajo sin decisiones previas. Al final vas a tener una herramienta funcionando en el Codespace que lee el repositorio entero, escribe los tests directamente en los archivos, los ejecuta y te muestra el resultado.

---

### 4.1 Qué herramienta y por qué esa

Evalué las opciones que un tester puede usar **sin pagar nada**:

| Herramienta | Veredicto |
|---|---|
| **Gemini CLI + clave de AI Studio** | ✅ **La elegida.** Agente de terminal, código abierto (Apache 2.0), corre dentro del Codespace, lee el repositorio completo, escribe archivos y ejecuta comandos. La clave gratuita de AI Studio da acceso a los modelos Flash, que alcanzan de sobra para generar tests. Ventaja adicional acá: el proyecto ya usa Gemini, así que es la misma cuenta y la misma llave. |
| **Antigravity CLI** | Alternativa directa si preferís entrar con la cuenta de Google sin generar clave. Es el destino al que Google movió el ingreso gratuito de cuentas personales a mediados de 2026. Cuota más chica y binario cerrado. |
| **GitHub Copilot** | Sirve para autocompletar mientras escribís, no para generar una suite entera. El plan gratuito es limitado. Complemento, no reemplazo. |
| **Google AI Studio (web)** | Gratis y sin instalar nada, pero hay que copiar y pegar cada archivo a mano. Útil para consultas puntuales; impracticable para 44 módulos. |
| **Claude Code / Codex CLI** | Mejores en tareas difíciles, pero requieren suscripción paga. Quedan fuera del criterio. |

> **Sobre las cuotas:** las condiciones de los planes gratuitos de Google cambiaron varias veces durante 2026 — el ingreso gratuito con cuenta personal a Gemini CLI se discontinuó el 18 de junio de 2026 y quedó el camino de la clave de AI Studio para los modelos Flash. Los números exactos ya no se publican en la documentación: se ven en el panel de AI Studio del proyecto. Tomá cualquier cifra que leas en un tutorial como orientativa y mirá tu panel.

---

### 4.2 Instalación, paso a paso

Todo esto se hace **dentro de la terminal del Codespace**, no en tu máquina.

**Paso 1 — Verificar Node**

```bash
node --version
```

Tiene que decir 20 o superior. El devcontainer del proyecto ya lo trae.

**Paso 2 — Instalar**

```bash
npm install -g @google/gemini-cli
```

Si el Codespace no deja instalar globalmente, se puede correr sin instalar nada:

```bash
npx @google/gemini-cli
```

**Paso 3 — Conseguir la clave**

Entrar a **aistudio.google.com** con una cuenta de Google, ir a la sección de claves de API y crear una nueva. En la misma pantalla se ven los límites vigentes del proyecto: anotalos, son los que van a aplicar.

**Paso 4 — Configurarla**

```bash
export GEMINI_API_KEY="la-clave-que-generaste"
echo 'export GEMINI_API_KEY="la-clave-que-generaste"' >> ~/.bashrc
```

La segunda línea hace que sobreviva a reabrir la terminal.

**Paso 5 — Arrancar**

```bash
cd /workspaces/Porota-trading
gemini
```

Se abre una sesión interactiva. La primera vez pregunta el método de autenticación: elegir la opción de clave de API.

**Paso 6 — Comprobar que lee el repositorio**

Escribí esto como primer mensaje:

```
Leé d_economics.py y decime en una frase qué hace calculate_trade_costs_pct().
```

Si responde algo coherente sobre costos de operación, está funcionando y tiene acceso a los archivos. Si dice que no encuentra el archivo, revisá que arrancaste `gemini` parado en la carpeta del proyecto.

---

### 4.3 Darle contexto permanente al agente

Antes de pedirle nada, creá un archivo `GEMINI.md` en la raíz del proyecto. Gemini CLI lo lee automáticamente en cada sesión, así que no hay que repetir el contexto cada vez:

```bash
cat > GEMINI.md <<'EOF'
# Contexto para el agente

Este es Porota Trading, un bot que opera con dinero real en el mercado
argentino (BYMA) a través del broker PPI.

## Tu tarea
Escribir tests unitarios con pytest. Los tests van en tests/.

## Reglas que no se negocian
1. NUNCA modifiques el código de producción para que un test pase.
   Si un test falla, el test encontró un bug: reportalo, no lo tapes.
2. NUNCA escribas un test que solo verifique que la función devuelve
   lo que la función devuelve. Cada test fija una PROPIEDAD que debe
   ser cierta siempre.
3. Cada test lleva un docstring que explica POR QUE importa esa
   propiedad y qué pasaría si se rompiera.
4. Los nombres de test son frases en castellano que describen la
   propiedad: test_el_tamano_nunca_supera_el_capital_disponible.
5. Ningún test puede salir a Internet ni tocar la cuenta real.
   Todo con datos controlados o simulados.

## Prioridad de riesgo
Clase A (maxima): logica que calcula dinero. Falla en silencio,
devuelve un numero plausible y equivocado. d_economics.py,
k_position_manager.py, ai_derivatives_engine.py, c_ppi_client.py.
Clase B: logica que decide si operar. aj_trade_gate.py, p_risk_guardian.py.
Clase C: todo lo demas.

## Estilo de referencia
Mirá tests/test_economics_costs.py y tests/test_derivatives_and_gate.py.
Replicá ese formato exacto.
EOF
```

Este archivo es lo que separa un agente que produce tests útiles de uno que produce ruido. Sin él, hay que repetir todo en cada prompt y el agente igual se olvida a mitad de camino.

---

### 4.4 Las cuatro preguntas — el corazón del método

**No le pidas tests. Pedile primero un análisis.** Esta es la diferencia entre una suite que encuentra bugs y una que solo infla la cobertura.

Para cada módulo, el primer mensaje es este:

```
Leé el módulo k_position_manager.py.

ANTES de escribir un solo test, respondeme estas cuatro preguntas.
No escribas código todavía.

1. ¿Qué PROPIEDADES de este módulo deben ser ciertas siempre,
   sin importar la entrada? Listámelas.

2. ¿Qué entrada podría producir un resultado PLAUSIBLE PERO
   INCORRECTO? No me interesa qué entrada tira una excepción:
   me interesa qué entrada devuelve un número creíble y equivocado.

3. ¿Qué pasa en los extremos? Cero, negativo, valores enormes,
   cadena vacía, None, listas vacías.

4. ¿Hay alguna operación donde se MEZCLEN UNIDADES? Porcentaje
   contra fracción, pesos contra dólares, segundos contra minutos,
   cantidad contra monto. Revisá cada suma y cada comparación.
```

**La pregunta 4 es la que más rinde en este proyecto.** Fue la que encontró el error más caro de la versión: una función que devolvía una fracción y se comparaba contra puntos porcentuales, haciendo que el filtro de rentabilidad descontara cien veces menos costo del real. No fallaba nunca. Solo devolvía un número creíble que estaba mal.

**Leé las respuestas antes de seguir.** Si el análisis es flojo o genérico, los tests van a serlo también: volvé a preguntar señalando qué falta. Si el análisis es bueno, recién ahí:

```
Perfecto. Ahora escribí un test por cada propiedad de la respuesta 1,
uno por cada caso de la respuesta 2 y uno por cada extremo de la
respuesta 3. Si encontraste algo en la respuesta 4, ese test va
primero y me lo marcás.

Guardalos en tests/test_position_manager.py siguiendo exactamente el
formato de tests/test_economics_costs.py.
```

---

### 4.5 Verificar que los tests sirven — el paso que nadie hace

Un test generado que pasa a la primera **no probó nada todavía**. Puede estar verificando aire. Hay que verlo fallar:

```
Corré los tests que acabás de escribir y mostrame el resultado.

Después, uno por uno:
1. Rompé a propósito la función que el test verifica (cambiá un
   signo, un redondeo, un límite).
2. Corré el test.
3. Confirmame que se puso ROJO.
4. Restaurá la función original.

Si algún test sigue en verde con la función rota, ese test no sirve:
reescribilo y avisame cuál era.
```

Esto se llama testing de mutación hecho a mano. Es tedioso y es exactamente donde está el valor: **un test que no se pone rojo cuando el código está roto es un test decorativo.**

Se puede automatizar sobre los módulos críticos:

```bash
pip install mutmut
mutmut run --paths-to-mutate d_economics.py
mutmut results
```

Cada mutante que sobrevive es un agujero en la suite.

---

### 4.6 El ciclo completo, módulo por módulo

Repetí esto para cada módulo de la lista de prioridad de la sección 1:

```
1. gemini                          → abrir sesión en la carpeta del proyecto
2. "Leé <módulo>. Antes de escribir un test, las cuatro preguntas."
3. Leer las respuestas. ¿Son específicas? Si no, repreguntar.
4. "Ahora escribí los tests siguiendo el formato de referencia."
5. "Corrélos y mostrame el resultado."
6. "Rompé cada función a propósito y confirmame que el test se pone rojo."
7. pytest -v                       → verificar vos mismo, fuera del agente
8. git add tests/ && git commit    → commitear SOLO la carpeta tests/
```

**El paso 8 tiene una razón concreta:** commiteá únicamente `tests/`. Si el agente tocó código de producción sin que lo pidieras, en el commit se ve y lo frenás ahí. Revisá siempre `git diff` antes de confirmar.

---

### 4.7 Cuando un test encuentra un bug de verdad

Va a pasar. Cuando pase, el orden importa:

```
1. NO arreglar el código todavía.
2. Confirmar que el test falla por el motivo correcto y no por
   estar mal escrito.
3. Commitear el test que falla, solo.
4. Recién ahí arreglar el código.
5. Ver el test ponerse verde.
6. Commitear el arreglo.
```

Así queda registrado en el historial que el bug existía y que el test lo detecta. Si arreglás primero, nunca vas a saber si el test realmente lo agarraba.

Prompt para el diagnóstico:

```
El test <nombre> está fallando. NO toques el test ni el código.
Explicame:
1. Qué esperaba el test y qué devolvió la función.
2. Cuál es la causa raíz, en la línea exacta.
3. Qué consecuencia tiene esto operando con dinero real:
   ¿pierde plata, deja de operar, o se cae?
4. Recién entonces, proponeme el arreglo mínimo.
```

---

### 4.8 Cuidar la cuota

Las sesiones agénticas consumen pedidos rápido: un solo prompt puede disparar muchas llamadas al modelo mientras el agente lee archivos y ejecuta comandos.

| Práctica | Por qué |
|---|---|
| Un módulo por sesión | Sesiones largas arrastran contexto que se recobra en cada llamada. |
| `GEMINI.md` en vez de repetir contexto | Se envía una vez por sesión, no en cada mensaje. |
| Pedir análisis antes que código | Un análisis cuesta un pedido; una suite mal generada cuesta diez y hay que descartarla. |
| Correr `pytest` vos mismo | No gastes pedidos del agente en algo que hace la terminal gratis. |
| Empezar por los módulos Clase A | Si la cuota se agota, que se agote en lo que importa. |

---

### 4.9 Qué NO delegarle al agente

| Nunca | Por qué |
|---|---|
| Modificar código de producción sin revisión | El agente optimiza para que el test pase, no para que el sistema sea correcto. La forma más rápida de hacer pasar un test es romper la función. |
| Cambiar valores de riesgo, límites o costos | Son decisiones de negocio, no técnicas. |
| Escribir tests que salgan a Internet o toquen la cuenta real | Un test que ordena de verdad no es un test. |
| Aceptar un test sin verlo fallar | Es el único paso que distingue un test real de uno decorativo. |
| Dejarlo correr sin mirar el `git diff` | Es la red de seguridad completa de este flujo de trabajo. |

## 5. Integración: probar contra las APIs reales

```bash
python ap_api_verifier.py --dry     # ver qué haría, sin llamar
python ap_api_verifier.py           # ejecutar de verdad
python ap_api_verifier.py --solo ppi
```

Genera `data/informe_apis.md` documentando de cada una de las 20 llamadas: qué se envía, qué se espera, qué módulo la usa, qué devolvió y cuánto tardó. Enmascara toda credencial en la salida.

**Ninguna verificación manda una orden real**: usa presupuesto y estimación, salvo en sandbox.

### Qué hacer con el resultado

| Resultado | Acción |
|---|---|
| 🟢 Todo verde | Seguir con la simulación. |
| 🔴 PPI, Gemini o base de datos | Bloqueante. Sin esos tres no tiene sentido probar nada más. |
| 🔴 Telegram | Bloqueante para el arranque: sin Telegram no se puede autorizar. |
| 🔴 IOL | No bloqueante. El sistema funciona sin IOL. |
| 🟡 Amarillos | Anotar y seguir; revisar al final. |

---

## 6. Aceptación: el modo simulación

Es la prueba de sistema completa. Todo el circuito real, sin riesgo.

```bash
python entrypoint.py
# responder "🧪 Simulación" en Telegram
# abrir el panel en /testing
```

### Guion de aceptación

| # | Qué verificar | Criterio de aprobación |
|---|---|---|
| 1 | El bot **no** opera antes de la autorización | La traza muestra «esperando autorización» y ninguna orden |
| 2 | Llega el mensaje a Telegram con los tres botones | Los tres responden |
| 3 | Un código de sesión equivocado no autoriza | Se rechaza |
| 4 | Semáforos de salud | PPI, base y Gemini en verde |
| 5 | Descubrimiento del universo | Cantidad razonable de instrumentos; permisos detectados |
| 6 | Cada descarte tiene motivo del catálogo | Ningún descarte sin código |
| 7 | Los costos descuentan ~1,65% + spread | Si parece de centésimas, hay regresión del bug de unidades |
| 8 | Las órdenes salen con identificador `SIM-` | **Ninguna orden en la cuenta real** |
| 9 | Stop-loss y take-profit calculados y registrados | Coherentes con el ATR |
| 10 | Cierre de rueda a las 17:00 | Reporte generado |
| 11 | Reiniciar el contenedor no pierde datos | El historial sigue estando |
| 12 | Kill switch manual | Corta, drena, reconcilia y avisa |

**Criterio de salida:** los 12 en verde durante **una rueda completa**, no un rato.

---

## 7. Automatización continua

Ya hay workflows en `.github/workflows/`. Verificar que incluyan:

```yaml
- run: ruff check .
- run: pytest --cov=. --cov-report=term-missing
- run: bandit -r . -ll
- run: pip-audit
```

Gratis en repositorios públicos y con minutos incluidos en privados.

**Regla:** si el CI está rojo, no se promueve a `main`. Sin excepciones — la primera excepción se convierte en costumbre.

---

## 8. Cómo reportar un bug acá

Para que un reporte sea accionable en este sistema hace falta más que «no funciona»:

1. **Clase** (A, B o C según la sección 0)
2. **Módulo y función**
3. **Entrada exacta** que lo produce
4. **Qué devolvió** y **qué debería devolver**
5. **Cómo se detectó**: test, simulación, verificador, panel
6. **Un test que lo reproduzca** — este es el punto importante: el test se escribe **antes** de arreglar el bug, se ve fallar, se arregla, y queda en la suite para siempre. Así el mismo bug no vuelve.

---

## 9. Definición de terminado

El sistema está listo para producción cuando:

- [ ] Los 63 tests actuales pasan
- [ ] `k_position_manager`, `c_ppi_client` y `p_risk_guardian` tienen tests de sus propiedades críticas
- [ ] `ruff check .` sin errores
- [ ] `bandit` sin hallazgos de severidad alta
- [ ] `pip-audit` sin vulnerabilidades críticas
- [ ] El verificador de APIs da verde en PPI, Gemini, base de datos y Telegram
- [ ] Los 12 puntos del guion de aceptación se cumplieron durante una rueda completa
- [ ] Se probó **restaurar** un respaldo, no solo hacerlo
- [ ] Las credenciales se rotaron y se movieron a Secrets
- [ ] El CI está en verde

---

## 10. Lo que yo probaría primero si tuviera un solo día

Por si hay que priorizar con tiempo limitado, en este orden:

1. **`k_position_manager._close_position()`** — es donde se calcula cuánta plata se ganó o se perdió. Un error acá corrompe todas las métricas y el aprendizaje se entrena sobre datos falsos.
2. **El mapeo de parámetros de orden por tipo de instrumento** en `c_ppi_client` — una orden mal armada se rechaza (barato) o se ejecuta distinto de lo pensado (caro).
3. **Los límites del kill switch** en `p_risk_guardian` — es la última línea de defensa. Si no corta cuando debe, no hay nada más abajo.

Los tres son Clase A: fallan en silencio y se descubren en el resumen de cuenta.
