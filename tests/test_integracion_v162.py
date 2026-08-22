"""
test_integracion_v162.py — Tests de integración (v16.2)

EL HALLAZGO QUE ESTOS TESTS EXISTEN PARA ATAJAR
-----------------------------------------------
La auditoría de la versión anterior nombró un patrón que explicaba la mitad de
sus hallazgos: el paquete tenía módulos excelentes que nadie llamaba.
aj_trade_gate, ai_derivatives_engine y validar_secretos_criticos estaban bien
escritos, bien pensados y bien testeados — y no se ejecutaban. La
documentación los describía como funcionalidades activas porque el código
existía.

Un test unitario sobre un módulo aislado pasa igual esté cableado o no. Ese es
exactamente el hueco: el sistema se auditaba bien por partes y no hacía lo que
decía el documento cuando corría entero.

Estos tests no prueban que los módulos funcionen — eso ya lo cubren los tests
unitarios. Prueban que estén CONECTADOS: que la función tenga un call-site
real en el camino que se ejecuta, que los contratos entre módulos coincidan y
que las decisiones de un módulo lleguen al siguiente.

Buena parte de esto se verifica por análisis estático del árbol de imports y
de las llamadas. Puede parecer poco ortodoxo para un test, y es deliberado: el
defecto que buscamos es precisamente la ausencia de una llamada, y una
ausencia no se detecta ejecutando lo que sí está.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

MODULOS = sorted(p.name for p in RAIZ.glob("*.py"))


def _sin_comentarios(nombre):
    """El contenido de un archivo con los comentarios quitados.

    POR QUÉ EXISTE, y es una lección del propio desarrollo de esta suite: la
    primera versión de estos tests buscaba texto con `in` sobre el archivo
    crudo, y CINCO de ellos fallaron sobre código correcto. El patrón que
    buscaban aparecía dentro de un comentario que explicaba justamente el
    defecto ya corregido.

    Un test que confunde "el código hace X" con "el archivo menciona X" es
    peor que no tenerlo: da verde el día que alguien escribe la frase correcta
    en un comentario sin cambiar una línea de lógica. Es exactamente el mismo
    error de fondo que la auditoría señaló en la documentación —describir como
    activo algo que existe pero no se ejecuta— aplicado a los tests.

    Para .py se usa el tokenizador, que es exacto. Para .yml y Dockerfile
    alcanza con descartar las líneas que empiezan con almohadilla.
    """
    ruta = RAIZ / nombre
    texto = ruta.read_text(encoding="utf-8")

    if ruta.suffix == ".py":
        # Se BLANQUEAN los tramos de comentario en su lugar, en vez de
        # reconstruir el archivo juntando tokens. Reconstruir rompe el
        # código: `secrets.randbelow(...)` sale como tres tokens separados y
        # una búsqueda por texto deja de encontrarlo. El primer intento de
        # este helper tenía justamente ese defecto y hacía fallar un test
        # sobre código correcto — el mismo tipo de falso positivo que el
        # helper existe para evitar.
        import io
        import tokenize
        try:
            comentarios = [t for t in tokenize.generate_tokens(
                io.StringIO(texto).readline) if t.type == tokenize.COMMENT]
        except tokenize.TokenError:
            return texto
        lineas = texto.splitlines()
        for tok in comentarios:
            fila = tok.start[0] - 1
            if 0 <= fila < len(lineas):
                lineas[fila] = lineas[fila][:tok.start[1]]
        return "\n".join(lineas)

    return "\n".join(l for l in texto.splitlines() if not l.strip().startswith("#"))


def _arbol(nombre):
    return ast.parse((RAIZ / nombre).read_text(encoding="utf-8"), filename=nombre)


def _llamadas_en(nombre):
    """Nombres de todas las funciones y métodos invocados en un archivo."""
    nombres = set()
    for nodo in ast.walk(_arbol(nombre)):
        if isinstance(nodo, ast.Call):
            f = nodo.func
            if isinstance(f, ast.Name):
                nombres.add(f.id)
            elif isinstance(f, ast.Attribute):
                nombres.add(f.attr)
    return nombres


def _llamadas_en_runtime(excluir=("tests",)):
    """Todas las llamadas del paquete, sin contar los tests.

    La exclusión es el punto entero del ejercicio: una función que solo se
    invoca desde tests/ es código muerto en producción, aunque tenga
    cobertura del 100%.
    """
    todas = set()
    for m in MODULOS:
        if any(m.startswith(e) for e in excluir):
            continue
        todas |= _llamadas_en(m)
    return todas


def _imports_de(nombre):
    modulos = set()
    for nodo in ast.walk(_arbol(nombre)):
        if isinstance(nodo, ast.Import):
            for a in nodo.names:
                modulos.add(a.name.split(".")[0])
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos.add(nodo.module.split(".")[0])
    return modulos


# ===========================================================================
# 1. EL PORTÓN OPERATIVO ESTÁ CABLEADO
# ===========================================================================

class TestPortonCableado:
    """P0-04: el catálogo de 29 casos no lo importaba ningún módulo del ciclo
    de trading. No había control de horario, de reloj ni de cierre de rueda:
    el bucle corría las 24 horas."""

    def test_j_main_importa_el_porton(self):
        assert "aj_trade_gate" in _imports_de("j_main.py"), (
            "j_main no importa el portón operativo. Los 29 códigos del "
            "catálogo y la sección entera del documento describen un "
            "subsistema que no se ejecuta.")

    def test_las_tres_funciones_del_porton_tienen_call_site_real(self):
        llamadas = _llamadas_en_runtime()
        for fn in ("check_session_health", "check_open_conditions",
                   "evaluate_news_context"):
            assert fn in llamadas, (
                f"{fn}() no se invoca desde ningún módulo de runtime. Un test "
                f"unitario sobre ella pasaría igual, y el sistema no la "
                f"ejecutaría nunca.")

    def test_existe_un_sensor_de_mercado_abierto(self):
        llamadas = _llamadas_en_runtime()
        assert "_mercado_abierto" in llamadas, (
            "Sin este sensor, check_session_health recibe market_open como un "
            "valor fijo y MERCADO_CERRADO no se dispara nunca.")

    def test_existe_un_sensor_de_desfasaje_de_reloj(self):
        llamadas = _llamadas_en_runtime()
        assert "_desfasaje_de_reloj" in llamadas, (
            "Con el reloj corrido, el control de cotización vieja deja de "
            "servir: compara el tick contra una hora local equivocada.")

    def test_las_abstenciones_se_registran_con_su_codigo(self):
        llamadas = _llamadas_en_runtime()
        assert "registrar_abstencion_global" in llamadas, (
            "Sin registro por código, el panel no puede mostrar lo que la "
            "documentación promete y la pregunta 'por qué no operó hoy' solo "
            "se responde interpretando logs.")


# ===========================================================================
# 2. EL MOTOR DE DERIVADOS ESTÁ CABLEADO
# ===========================================================================

class TestDerivadosCableados:
    """P0-02: size_long_option, size_future, must_close_for_expiry,
    portfolio_room_for_derivatives y evaluar_capacidad_operativa no tenían
    ningún call-site fuera de tests/. Con TRADE_DERIVATIVES=true, una opción
    se dimensionaba con la fórmula de acciones: sin lote y sin usar la prima
    como pérdida máxima."""

    @pytest.mark.parametrize("funcion", [
        "size_long_option",
        "size_future",
        "portfolio_room_for_derivatives",
        "assess_derivative_eligibility",
    ])
    def test_la_funcion_se_invoca_en_runtime(self, funcion):
        assert funcion in _llamadas_en_runtime(), (
            f"{funcion}() solo se llama desde tests. El camino de derivados "
            f"del documento describe algo que el código no ejecuta.")

    def test_el_dimensionamiento_se_ramifica_por_clase(self):
        fuente = (RAIZ / "j_main.py").read_text(encoding="utf-8")
        assert 'inst.asset_class in ("OPCIONES", "FUTUROS")' in fuente, (
            "No hay bifurcación por clase de activo antes de dimensionar: "
            "todo se dimensiona con la fórmula de renta variable.")

    def test_el_lote_del_subyacente_llega_al_parseo(self):
        fuente = (RAIZ / "m_instrument_universe.py").read_text(encoding="utf-8")
        assert "tipo_subyacente=" in fuente, (
            "parse_option_ticker se invoca sin tipo_subyacente, así que "
            "lote_por_subyacente('') devuelve 100 también para CEDEARs. El "
            "sistema creería arriesgar el 1% y arriesgaría el 10%.")

    def test_los_futuros_consultan_el_multiplicador_al_mercado(self):
        assert "av_rofex_client" in _llamadas_en_runtime() or \
            "av_rofex_client" in (RAIZ / "m_instrument_universe.py").read_text(
                encoding="utf-8"), (
            "Sin el conector de Matba Rofex no llega el multiplicador de "
            "contrato, y sin multiplicador no hay dimensionamiento posible.")


# ===========================================================================
# 3. LA VALIDACIÓN DE CREDENCIALES CORRE AL ARRANCAR
# ===========================================================================

class TestValidacionDeArranque:
    """P0-09: validar_secretos_criticos() estaba completa y no tenía ningún
    call-site. El código SECRETOS_FALTANTES del nivel 0 no existía en tiempo
    de ejecución: un token truncado al copiar no se detectaba al arrancar
    sino cuando hacía falta mandar el aviso de un kill switch."""

    def test_el_entrypoint_valida_antes_de_levantar_procesos(self):
        fuente = (RAIZ / "entrypoint.py").read_text(encoding="utf-8")
        assert "validar_secretos_criticos" in fuente
        assert fuente.index("validar_secretos_criticos") < fuente.index("Popen"), (
            "La validación tiene que correr ANTES de levantar los procesos. "
            "Después no sirve: ya arrancó.")

    def test_el_panel_valida_su_propia_configuracion(self):
        fuente = (RAIZ / "o_dashboard.py").read_text(encoding="utf-8")
        assert "validar_configuracion" in fuente, (
            "El panel corre en un proceso distinto: si no valida por su "
            "cuenta, puede levantar sin contraseña.")

    def test_el_panel_falla_cerrado_sin_credenciales(self):
        import ay_dashboard_auth as auth
        original = (auth.PASSWORD_HASH, auth.PASSWORD_PLANA, auth.TOKEN_BEARER)
        try:
            auth.PASSWORD_HASH = auth.PASSWORD_PLANA = auth.TOKEN_BEARER = ""
            problemas = auth.validar_configuracion()
            assert problemas, (
                "Sin ninguna credencial configurada el panel arrancaría sin "
                "protección. Un panel desprotegido que arranca es peor que uno "
                "que no arranca: el segundo se nota enseguida.")
        finally:
            auth.PASSWORD_HASH, auth.PASSWORD_PLANA, auth.TOKEN_BEARER = original


# ===========================================================================
# 4. UN SOLO LECTOR DE TELEGRAM
# ===========================================================================

class TestLectorUnicoDeTelegram:
    """P0-03: había dos lectores de getUpdates con offsets independientes.
    Telegram consume las actualizaciones al entregarlas, así que cada uno se
    comía los mensajes del otro de forma no determinística."""

    def test_solo_un_modulo_llama_a_getupdates(self):
        lectores = [m for m in MODULOS
                    if not m.startswith("test")
                    and "getUpdates" in (RAIZ / m).read_text(encoding="utf-8")
                    and "requests.get" in (RAIZ / m).read_text(encoding="utf-8")]
        assert lectores == ["b_notifiers.py"], (
            f"Hay más de un lector de getUpdates: {lectores}. El síntoma no es "
            f"un error visible sino botones de confirmación que a veces "
            f"funcionan y a veces no.")

    def test_no_quedo_el_bucle_de_escucha_paralelo(self):
        fuente = (RAIZ / "ar_telegram_commands.py").read_text(encoding="utf-8")
        assert "def arrancar_escucha" not in fuente, (
            "El segundo lector sigue definido. Además de competir por las "
            "actualizaciones, desempaquetaba mal la tupla y fallaba con "
            "AttributeError en cada vuelta.")

    def test_el_lector_devuelve_tupla_y_el_consumidor_la_desempaqueta(self):
        fuente = (RAIZ / "l_order_confirmation.py").read_text(encoding="utf-8")
        assert "taps, new_offset = notifier.get_telegram_button_taps" in fuente, (
            "El contrato es (taps, next_offset). Consumirlo como una lista "
            "produce AttributeError en cada vuelta, absorbido por el except.")

    def test_los_mensajes_de_texto_se_despachan(self):
        fuente = (RAIZ / "l_order_confirmation.py").read_text(encoding="utf-8")
        assert 'tap.get("text")' in fuente and "procesar_mensaje" in fuente, (
            "Sin esto, los comandos escritos (PARADA, ESTADO, LIQUIDAR) nunca "
            "se leen: la parada de emergencia no responde a nada.")

    def test_los_codigos_de_confirmacion_no_son_predecibles(self):
        fuente = _sin_comentarios("ar_telegram_commands.py")
        assert "int(time.time()) % 100000" not in fuente, (
            "Códigos derivados del reloj: cualquiera que sepa la hora los "
            "reproduce. Con la lectura de texto habilitada, eso permite "
            "LIQUIDAR desde un tercero.")
        assert "secrets.randbelow" in fuente

    def test_el_interprete_valida_el_remitente_por_su_cuenta(self):
        fuente = (RAIZ / "ar_telegram_commands.py").read_text(encoding="utf-8")
        assert "_remitente_autorizado" in fuente, (
            "Defensa en profundidad: el lector puede cambiar, y una "
            "validación que vive en un solo lugar deja de existir el día que "
            "alguien refactoriza ese lugar.")


# ===========================================================================
# 5. EL KILL SWITCH MIDE PATRIMONIO
# ===========================================================================

class TestKillSwitchSobrePatrimonio:
    """P0-01, criticidad 10/10: el kill switch comparaba el saldo en pesos
    contra la foto del saldo en pesos de la apertura. Comprar reduce la caja,
    así que la primera orden del día declaraba 'pérdida diaria' y activaba un
    corte financiero, que nunca se auto-libera."""

    def test_el_guardian_usa_el_modulo_de_patrimonio(self):
        assert "ax_equity" in _imports_de("p_risk_guardian.py")

    def test_ya_no_se_mide_contra_daily_balance_snapshot(self):
        fuente = (RAIZ / "p_risk_guardian.py").read_text(encoding="utf-8")
        assert "daily_balance_snapshot" not in fuente, (
            "Esa tabla guarda el renglón de EFECTIVO. Medir pérdida diaria "
            "contra efectivo es la causa raíz del falso positivo.")

    def test_comprar_no_genera_perdida_diaria(self):
        """La propiedad, ejecutada contra un cliente de mentira.

        Se simula el escenario exacto que rompía: la cuenta abre con todo en
        efectivo y a media rueda una parte se convirtió en tenencia. El
        patrimonio no cambió; la caja bajó un 40%.
        """
        import ax_equity

        class ClienteFalso:
            def get_available_balance(self):
                return [{"name": "Pesos", "simbol": "ARS", "amount": 600_000}]

            def get_portfolio(self):
                return [{"ticker": "GGAL", "amount": 400_000, "currency": "ARS",
                         "quantity": 100}]

            def get_movements(self, desde, hasta):
                return []

        equity = ax_equity.patrimonio_actual(ClienteFalso())
        assert equity.utilizable
        assert equity.total == 1_000_000, (
            f"El patrimonio debería ser 1.000.000 (600k de caja + 400k de "
            f"tenencia) y dio {equity.total}. Con la caja sola daría 600.000, "
            f"un 40% de 'pérdida' que nunca ocurrió.")

    def test_una_tenencia_sin_valuar_no_se_cuenta_como_cero(self):
        """Sumar cero por una posición que el bróker no valuó equivale a
        afirmar que no vale nada. Tiene que ser un dato faltante."""
        import ax_equity

        class ClienteIncompleto:
            def get_available_balance(self):
                return [{"name": "Pesos", "simbol": "ARS", "amount": 500_000}]

            def get_portfolio(self):
                return [{"ticker": "AL30", "quantity": 100}]  # sin valuación

            def get_movements(self, desde, hasta):
                return []

        equity = ax_equity.patrimonio_actual(ClienteIncompleto())
        assert not equity.utilizable
        assert "AL30" in equity.motivo

    def test_un_patrimonio_parcial_no_se_persiste(self):
        """Escribir una foto parcial convertiría un dato faltante en un dato
        malo, y mañana nadie sabría que ese número nunca fue el patrimonio."""
        import ax_equity
        parcial = ax_equity.Equity(efectivo_ars=100.0, total=100.0, parcial=True,
                                   motivo="prueba")
        assert ax_equity.guardar_snapshot(parcial) is False


# ===========================================================================
# 6. EL TARIFARIO LLEGA AL FILTRO DE RENTABILIDAD
# ===========================================================================

class TestTarifarioCableado:
    """P1-12: una sola comisión para todas las clases. El filtro de
    rentabilidad neta, corazón declarado del diseño, trabajaba con un número
    que no correspondía al instrumento que evaluaba."""

    def test_economics_delega_en_el_tarifario(self):
        assert "au_fee_schedule" in (RAIZ / "d_economics.py").read_text(
            encoding="utf-8")

    def test_la_clase_de_activo_viaja_hasta_el_dimensionamiento(self):
        fuente = (RAIZ / "j_main.py").read_text(encoding="utf-8")
        assert "asset_class=inst.asset_class" in fuente, (
            "Si la clase no llega, el dimensionamiento descuenta la fricción "
            "de renta variable a un bono y a una opción por igual.")

    def test_el_bono_y_la_opcion_no_pagan_lo_mismo(self):
        import au_fee_schedule as t
        assert t.costo_redondo("BONOS") < t.costo_redondo("ACCIONES") < \
            t.costo_redondo("OPCIONES")


# ===========================================================================
# 7. FRENO DE SIMULACIÓN Y ESTADO DETENIDO
# ===========================================================================

class TestFrenoDeSimulacion:
    """P1-20: interceptar_orden devolvía None (o sea 'ejecutá de verdad') en
    modo DETENIDO, que es el estado en el que el usuario respondió 'No
    arrancar'. El caso más peligroso quedaba fuera del punto único de
    control."""

    def test_la_condicion_esta_invertida_a_favor_de_la_seguridad(self):
        fuente = (RAIZ / "ao_startup_gate.py").read_text(encoding="utf-8")
        assert "if modo == MODO_REAL:\n        return None" in fuente, (
            "La orden tiene que salir SOLO en modo REAL. Una lista de estados "
            "prohibidos hay que acordarse de mantenerla; una lista de un solo "
            "estado permitido no se puede olvidar de actualizar.")

    def test_el_modo_detenido_intercepta(self):
        import copy
        import ao_startup_gate as gate
        original = copy.deepcopy(getattr(gate, "_estado", None))
        try:
            try:
                gate.autorizar(gate.MODO_DETENIDO, origen="test")
            except Exception:
                # Si autorizar exige un codigo valido, se fuerza el estado: lo
                # que se prueba aca es interceptar_orden, no el porton.
                if hasattr(gate._estado, "modo"):
                    gate._estado.modo = gate.MODO_DETENIDO
            orden = gate.interceptar_orden("GGAL", 100, 7000.0, "COMPRA", "ACCIONES")
            assert orden is not None, (
                "En modo DETENIDO la orden se escapo al mercado real. Es el "
                "estado en el que el usuario respondio 'No arrancar'.")
            assert orden.get("simulada") is True
            assert orden.get("bloqueada_por") == gate.MODO_DETENIDO
        finally:
            if original is not None:
                for campo, valor in vars(original).items():
                    setattr(gate._estado, campo, valor)


# ===========================================================================
# 8. NOTICIAS Y CONTEXTO FUERA DEL BUCLE POR INSTRUMENTO
# ===========================================================================

class TestLlamadasFueraDelBucle:
    """P1-13: fetch_latest_headlines y la llamada al modelo se invocaban una
    vez por instrumento. Con 40 instrumentos, unos 600 barridos de RSS y hasta
    600 llamadas al modelo por hora — muy por encima de cualquier cuota."""

    def test_los_titulares_se_traen_una_vez_por_vuelta(self):
        fuente = (RAIZ / "j_main.py").read_text(encoding="utf-8")
        cuerpo = fuente[fuente.index("def evaluate_instrument"):
                        fuente.index("def main(")]
        assert "news_cached if news_cached is not None" in cuerpo, (
            "evaluate_instrument sigue trayendo los titulares por su cuenta.")

    def test_existe_el_contexto_macro_cacheado_del_dia(self):
        assert "evaluar_contexto_macro_del_dia" in _llamadas_en_runtime()


# ===========================================================================
# 9. INVENTARIO CONSISTENTE
# ===========================================================================

class TestInventario:
    """P1-24: la portada declaraba 46 módulos, la ficha técnica enumeraba 44 y
    /health devolvía la versión 15.0. Ninguna afecta la operación; todas
    afectan la auditabilidad, que es el objetivo declarado del documento."""

    def test_la_version_sale_de_un_solo_lugar(self):
        fuente = (RAIZ / "o_dashboard.py").read_text(encoding="utf-8")
        assert 'VERSION = "16.2"' in fuente
        assert '"version": "15.0"' not in fuente

    def test_el_conteo_de_modulos_es_verificable(self):
        modulos = [m for m in MODULOS if not m.startswith("test")]
        assert len(modulos) >= 45, (
            f"Se contaron {len(modulos)} módulos. El número del documento se "
            f"genera desde acá, no se escribe a mano.")


# ===========================================================================
# 10. HIGIENE DEL PAQUETE
# ===========================================================================

class TestHigiene:
    """P0-05: credenciales reales versionadas y el control de secretos del CI
    que no las alcanzaba."""

    def test_no_hay_archivos_de_entorno_en_el_paquete(self):
        sobrantes = [p.name for p in RAIZ.glob(".env*") if p.name != ".env.example"]
        assert not sobrantes, f"Archivos de entorno en el paquete: {sobrantes}"

    def test_el_gitignore_cubre_la_familia_env(self):
        gi = (RAIZ / ".gitignore").read_text(encoding="utf-8")
        assert ".env.*" in gi and "!.env.example" in gi, (
            "El patrón .env solo excluye un nombre. .env.testing entraba al "
            "repositorio sin que nada avisara.")

    def test_existe_dockerignore(self):
        di = RAIZ / ".dockerignore"
        assert di.exists(), (
            "Sin .dockerignore, el COPY . . mete archivos de entorno, la base "
            "y el índice vectorial dentro de la imagen.")
        contenido = di.read_text(encoding="utf-8")
        assert ".env" in contenido and "data/" in contenido

    def test_el_dockerfile_no_instala_nada_por_fuera_de_requirements(self):
        df = _sin_comentarios("Dockerfile")
        instalaciones = [l for l in df.splitlines()
                         if "pip install" in l and "requirements.txt" not in l]
        assert not instalaciones, (
            f"Instalaciones sueltas en el Dockerfile: {instalaciones}. Dos "
            f"builds en semanas distintas producirían sistemas distintos.")

    def test_no_quedan_restos_de_codespaces(self):
        assert not (RAIZ / ".devcontainer").exists()
        for wf in (RAIZ / ".github" / "workflows").glob("*.yml"):
            assert "codespace" not in wf.read_text(encoding="utf-8").lower()


# ===========================================================================
# 11. PIPELINE
# ===========================================================================

class TestPipeline:
    """P0-07, P0-08 y P0-10."""

    def _wf(self, nombre):
        return (RAIZ / ".github" / "workflows" / nombre).read_text(encoding="utf-8")

    def test_el_ci_ejecuta_la_suite(self):
        assert "pytest" in self._wf("ci.yml"), (
            "Nada impide promover a producción una versión con los tests en "
            "rojo si el CI no los corre.")

    def test_el_despliegue_no_se_dispara_con_un_push(self):
        deploy = _sin_comentarios(".github/workflows/deploy.yml")
        assert "workflow_dispatch" in deploy
        assert "branches: [main]" not in deploy, (
            "Producción no se dispara por un simple push a main: es un "
            "principio de arquitectura declarado del propio sistema.")
        assert "DESPLEGAR" in deploy

    def test_la_verificacion_de_salud_corre_en_el_servidor(self):
        deploy = _sin_comentarios(".github/workflows/deploy.yml")
        assert "ssh-action" in deploy
        posicion_ssh = deploy.index("ssh-action")
        posicion_health = deploy.index("/health")
        assert posicion_health > posicion_ssh, (
            "El curl al /health tiene que estar DENTRO de la sesión SSH. En la "
            "máquina efímera de GitHub no hay ni bot ni Docker del proyecto: "
            "no verifica nada y no revierte nada.")

    def test_la_vuelta_atras_es_sobre_la_version_anterior(self):
        deploy = _sin_comentarios(".github/workflows/deploy.yml")
        assert "ANTERIOR" in deploy and "git checkout --detach \"$ANTERIOR\"" in deploy, (
            "Un `up -d` del mismo código roto reinicia lo que ya no funciona.")

    def test_el_barrido_de_secretos_cubre_todo_el_arbol(self):
        ci = self._wf("ci.yml")
        assert "grep -rEIn" in ci, (
            "El barrido anterior solo miraba py/yml/json y nunca abría el "
            "archivo que tenía los secretos.")
