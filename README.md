# Porota Trading v16.2

Sistema autónomo de trading algorítmico para el mercado argentino (BYMA), con
ejecución a través de PPI y motor de decisión sobre Google Gemini.

## Control desde Telegram

| Comando | Qué hace |
|---|---|
| `ESTADO` | Qué hay abierto y en qué estado está el bot |
| `PARADA` | Parada de emergencia: ordenada, liquidar todo, o corte seco |
| `REARRANCAR` | Volver a operar |
| `AYUDA` | La lista completa |

## Lo primero que hay que saber

**El bot no arranca a operar solo.** Al levantar, manda un mensaje a Telegram y
espera que autorices cómo arrancar: simulación contra el sandbox de PPI, real, o
quedarse levantado sin operar. Hasta que no respondas, no toca el mercado.

## Puesta en marcha en Docker

```bash
cp .env.example .env            # 0. crear configuracion local sin credenciales
docker compose build            # 1. construir el entorno reproducible
docker compose run --rm bot pytest -q  # 2. ejecutar la suite
docker compose up -d            # 3. arrancar; requiere credenciales en .env
docker compose exec bot python ap_api_verifier.py  # 4. verificar APIs
```

Responder **🧪 Simulación** y mirar el panel en `/testing`.

## Documentación

| Archivo | Qué contiene |
|---|---|
| `PLAN_DE_TESTING.md` | Plan completo para QA: por dónde empezar, herramientas gratuitas, guía paso a paso para generar tests con IA, guion de aceptación |
| `.env.example` | Plantilla sin secretos para crear el `.env` local |

**El Documento Maestro y la Bitácora se entregan por separado**, fuera de este
paquete, porque el Anexo A del Documento Maestro contiene credenciales en claro
y un PDF con claves subido a un repositorio queda en el historial de Git de
forma permanente.

## Panel de control

| Ruta | Para qué |
|---|---|
| `/` | Resumen de los últimos días |
| `/vivo` | Qué está haciendo el bot ahora mismo |
| `/testing` | Estado del arranque y traza paso a paso |
| `/salud` | Semáforos de todas las APIs y módulos |
| `/sre` | Propuestas de mejora e historial de cambios |
| `/historicos` | Qué hay archivado y de qué fuentes |
| `/aprendizaje` | Descarga del blog de aprendizaje |
| `/config` | Editor de variables de entorno |

## Credenciales

Para el **entorno de pruebas**, crear `.env` desde `.env.example` y cargar las
credenciales fuera de Git. Nunca versionar `.env`, `.env.testing` ni respaldos
del archivo de entorno.

Para **producción** hay que generar credenciales nuevas y moverlas a Secrets.
Las de prueba quedan en el historial del repositorio de forma permanente, así
que rotarlas antes del pase no es opcional.
