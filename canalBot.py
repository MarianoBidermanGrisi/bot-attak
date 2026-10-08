#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 canalBot - BOT MONOLITICO (archivo unico) Bitget + Telegram + Render
==============================================================================
 Fichero: canalBot.py
 Proposito:
   1) Extrae TODO lo relacionado con Bitget/Render/Telegram del bot original
      `bot_bb_cm/botbb_engine.py` (conexion, ordenes, SL/TP, trailing, BE,
      balance, TOP100 por volumen, OHLCV, Telegram, CSV/JSON, logging, env).
   2) CONSERVA la logica completa de VISUALIZACION de graficos de botbb_engine
      (matplotlib dark-theme, paneles, envio de foto a Telegram).
   3) CONSERVA todos los calculos MATEMATICOS de la logica de trading de
      botbb_engine (Heikin Ashi, Bollinger, EMA/MACD, VWAP semanal, RSI,
      StochRSI, divergencias) - COMENTADOS en espanol y SIN APLICAR.
      >>> REGLA ESTRICTA: la logica de trading de botbb_engine
          (detect_signal / _scan_side_arrays / _check_divergence) NO se
          aplica en este bot. Solo se conservan sus matematicas.
   4) ESTRATEGIA ACTIVA = SignalVWAP (TradingView Pine v5 traducido):
      EMAs 9/14/50/100 + VWAP anclado + estructura de pivotes +
      Canal de Regresion Logaritmica + senales LONG/SHORT.
   5) Las senales se VISUALIZAN en imagenes de Telegram estilo TradingView
      (velas + canal relleno + triangulos LONG/SHORT + VWAP + eje derecho).

 MAPA DE SECCIONES (orden numerado):
   [0]  ENCABEZADO / MAPA DEL FICHERO  (este bloque)
   [1]  IMPORTACIONES (bibliotecas)
   [2]  SISTEMA DE FLAGS Y WARNINGS (origen: CODIGO | BIBLIOTECA | DATOS)
   [3]  CONFIGURACION MANUAL DE PARAMETROS (EMA, TP, BE, trailing, margen...)
   [4]  LOGGING (sustituye print() - obligatorio en Render)
   [5]  BLACKLIST de activos NO-crypto en Bitget
   [6]  VARIABLES DE ENTORNO (Bitget / Telegram / Render)
   [7]  CALCULOS MATEMATICOS PRESERVADOS de botbb_engine  [NO APLICADOS]
   [8]  MOTOR BITGET/TELEGRAM/RENDER extraido de botbb_engine
   [9]  ESTRATEGIA ACTIVA SignalVWAP (Pine v5 -> Python)
   [10] VISUALIZACION: [10A] grafico activo estilo TradingView
                       [10B] grafico original de botbb (PRESERVADO)
   [11] LOOP PRINCIPAL ASYNC
   [12] MAIN / ARRANQUE

 DEPLOY (Render.com) - el usuario ejecuta manualmente:
   - Runtime: Python 3.10+
   - Build:   pip install -r requirements.txt
   - Start:   python canalBot.py
   - Env vars: BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE,
               TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (nunca en el codigo)
==============================================================================
"""

# =============================================================================
# [1] IMPORTACIONES - Bibliotecas externas y de sistema
# =============================================================================
import os                      # Sistema  - variables de entorno (Render/Bitget)
import csv                     # Sistema  - exportacion de trades a CSV
import json                    # Sistema  - persistencia de entradas abiertas
import math                    # Sistema  - matematicas (log/exp/sqrt)
import time                    # Sistema  - timestamps y sleeps
import asyncio                 # Sistema  - bucle asincrono (concurrencia)
import signal as signal_mod    # Sistema  - SIGTERM (Render lo envia al redesplegar)
import threading                # Sistema  - detectar hilo principal (bot_web_service)
import logging                 # Sistema  - logging estructurado (REEMPLAZA print)
import warnings                # Sistema  - captura de warnings de bibliotecas
from datetime import datetime  # Sistema  - marcas de tiempo de trades
from io import BytesIO         # Sistema  - buffer en memoria para el PNG
from typing import Optional    # Sistema  - tipado

import sl_tp                   # MODULO PROPIO - TODA la logica de SL/TP
                               # (entrada, break even, trailing, validacion).
                               # Editar sl_tp.py para cambiar el trading.

import numpy as np             # BIBLIOTECA - calculos vectorizados
import pandas as pd            # BIBLIOTECA - DataFrames OHLCV
import ccxt                    # BIBLIOTECA - cliente sincrono de Bitget
import ccxt.async_support as ccxt_async  # BIBLIOTECA - cliente asincrono
import aiohttp                 # BIBLIOTECA - HTTP asincrono (Telegram)
from ccxt import (             # BIBLIOTECA - clases de error de ccxt
    BadRequest,                #   HTTP 400 - peticion invalida
    AuthenticationError,       #   HTTP 401 - API keys invalidas
    PermissionDenied,          #   HTTP 403 - sin permisos
    RateLimitExceeded,         #   HTTP 429 - Rate Limit de Bitget
    ExchangeError,             #   HTTP 5xx - error del exchange
    ExchangeNotAvailable,      #   Exchange caido / mantenimiento
    NetworkError,              #   Errores de red / DNS
    RequestTimeout,            #   Timeout HTTP
    DDoSProtection,            #   Proteccion anti-DDoS de Bitget
)

# Graficos sin entorno grafico (obligatorio en Render/servidores)
import matplotlib
matplotlib.use('Agg')          # BIBLIOTECA - backend sin pantalla (sin GUI)
import matplotlib.pyplot as plt  # BIBLIOTECA - dibujo de graficos PNG


# =============================================================================
# [2] SISTEMA DE FLAGS Y WARNINGS - Diferencia CODIGO vs BIBLIOTECA vs DATOS
# =============================================================================
# Origenes posibles de cada incidencia detectada:
ORIGEN_CODIGO = "CODIGO"          # Bug dentro de este fichero
ORIGEN_BIBLIOTECA = "BIBLIOTECA"  # Bug/limitacion de una libreria externa
ORIGEN_DATOS = "DATOS"            # Datos invalidos/ausentes del mercado

# Modulos de Python que consideramos "bibliotecas" (auto-deteccion de origen)
_MODULOS_BIBLIOTECA = {
    "ccxt", "matplotlib", "pandas", "numpy", "aiohttp", "asyncio",
    "dateutil", "pytz", "PIL",
}

# Contador global de flags por codigo (para el resumen de salud)
FLAG_COUNTS: dict = {}


def origen_de_excepcion(e: Exception) -> str:
    """[2.1] Clasifica una excepcion: ¿pertenece a una biblioteca o al codigo?"""
    mod = (type(e).__module__ or "").split(".")[0]
    return ORIGEN_BIBLIOTECA if mod in _MODULOS_BIBLIOTECA else ORIGEN_CODIGO


# Nota: la funcion flag() se define en [4] justo despues de crear el logger,
# porque necesita el objeto _log para emitir los mensajes.

def _showwarning_adaptado(message, category, filename, lineno, file=None, line=None):
    """[2.3] Captura warnings nativos de Python (warnings.warn) y los reenvia
    al sistema de FLAGS clasificando automaticamente su origen."""
    mod = getattr(category, "__module__", "") or ""
    origen = ORIGEN_BIBLIOTECA if mod.split(".")[0] in _MODULOS_BIBLIOTECA else ORIGEN_CODIGO
    flag(f"WARN-{category.__name__}", origen,
         f"{category.__name__}: {message} ({os.path.basename(filename)}:{lineno})")


# Nota: la asignacion `warnings.showwarning = _showwarning_adaptado` se hace
# en la seccion [4], DESPUES de definir flag() (si no, NameError en runtime).


# =============================================================================
# [3] CONFIGURACION MANUAL DE PARAMETROS (todo editable aqui mismo)
# =============================================================================
CONFIG = {
    # -- [3.1] SENAL: EMAs del indicador SignalVWAP (TradingView) --
    "ema_fast": 20,                  # EMA 1  (cruza contra la de 100)
    "ema_mid": 50,                  # EMA 2  (filtro: debe ser > (long) o < (short) que la de 50)
    "ema_slow": 100,                 # EMA 3  (referencia media)
    "ema_cross": 200,               # EMA 4  (cruce rapida vs lenta)

    # -- [3.2] VWAP anclado (Anchor Period del indicador) --
    "vwap_anchor": "Session",       # Session|Week|Month|Quarter|Year|Decade|Century
                                    # (Earnings/Dividends/Splits: NO aplica a crypto -> ver [9.2])
    "vwap_anchor_offset_hours": 0,  # Desplazamiento horario del corte de sesion (0=UTC)

    # -- [3.3] Estructura de pivotes (BOS/CHOCH) --
    "swings_length": 50,            # Longitud de leg para pivotes SWING (50 en Pine)
    "internal_length": 5,           # Longitud de leg para pivotes INTERNAL (5 en Pine)

    # -- [3.4] Canal de Regresion Logaritmica --
    "channel_dev_mult": 2.0,        # Multiplicador de desviaciones (2.0 en Pine)
    "channel_line_width": 1.0,      # Grosor de linea del canal
    "channel_extend": "none",       # right|both|left|none (extension visual del canal)
    "channel_max_history": 4999,    # Ventana maxima de calculo del canal (4999 en Pine)

    # -- [3.5] Senales / Escaneo --
    "timeframe": "5m",              # Timeframe de velas (las imagenes de referencia usan 5m)
    "top_symbols_count": 100,       # TOP N cryptos por volumen 24h en Bitget
    "ohlcv_limit": 1500,            # Cantidad de velas a descargar por simbolo
    "scan_interval_sec": 300,       # Cada cuantos segundos se re-escanea el TOP 100
    "max_concurrent_fetches": 10,   # Concurrencia maxima de descargas (semaforo)

    # -- [3.5b] Filtro MACD Signal Line (condicion de entrada en 15m) --
    "macd_filter_enabled": True,    # LONG exige Signal Line VERDE en 15m;
                                    # SHORT exige Signal Line ROJA en 15m
    "macd_filter_tf": "15m",        # SOLO esta regla se mide en 15m; el resto
                                    # de la estrategia sigue en "timeframe" (5m)
    "macd_filter_limit": 300,       # velas 15m descargadas SOLO si hay senal 5m

    # -- [3.6] Riesgo / Gestion (configurable a mano) --
    "trading_habilitado": True,     # *** MAESTRO: True=ORDENES REALES en Bitget
                                    #             False=solo alertas+graficos ***
                                    # (sigue exigiendo BITGET_API_KEY/SECRET/PASS
                                    #  en variables de entorno para operar)
    "risk_pct": 0.07,               # 7% del colateral arriesgado por trade
    "leverage": 10.0,               # Apalancamiento 10x en Bitget
    "max_open_positions": 3,        # Maximo de posiciones abiertas simultaneas
    "sl_buffer_pct": 0.0070,        # Buffer "un poco por debajo/encima" del
                                    # pivot que toco el borde del canal (0.7%)
    "min_sl_dist_pct": 0.05,        # [SL FIJO 5%] LONG y SHORT: el SL siempre
                                    # queda a 5% de movimiento de precio
    "sl_max_dist_pct": 0.05,        # min = max => SIEMPRE 5% exacto (clamp de
                                    # sl_tp.calcular_sl_tp pisa pivote/canal).
                                    # Con leverage=10x: 5% x 10 = 50% del
                                    # margen asignado al trade (50% de perdida
                                    # sobre ESE capital); el SL salta ANTES
                                    # que la liquidacion (~10%).
    "pivot_touch_tol": 0.005,       # Tolerancia para dar por valido que un
                                    # pivot "toco" el borde del canal (0.5%)

    # -- [3.6b] TP1 / TP2 (DOS take profits porcentuales) --
    # [TPO-2] sustituyen al TP estructural (pivote/borde/rr_ratio).
    # Ejecucion en manage_positions via sl_tp.tp_accion():
    "tp1_pct": 0.02,                # TP1: +2% de precio a favor (10x -> +20%
                                    # de PnL sobre el margen del trade)
    "tp1_close_frac": 0.5,          # en TP1 se cierra el 50% de la posicion
    "tp2_pct": 0.03,                # TP2: +3% de precio -> se cierra el
                                    # RESTO (posicion completa)
    "tp_mode": "exchange",          # [TPO-3] "exchange": TP1/TP2 se colocan
                                    # como ordenes profit_plan EN BITGET
                                    # (visibles en el panel y las ejecuta el
                                    # exchange). "bot": las ejecuta el bot en
                                    # manage_positions (TPO-2). Fallback
                                    # automatico a "bot" si el exchange no
                                    # acepta los TP (notional < minimo).
    "tp_reconcile_sec": 600,        # [TPO-3] cada cuantos segundos manage
                                    # verifica que los TP sigan en Bitget
    "tp_retry_sec": 3,              # [FIX-H7] espera tras un intento de carga
                                    # de TP fallido por error TRANSITORIO
                                    # (429/500/red); 0 = reintento inmediato.
                                    # Los fallos DEFINITIVOS (notional/step)
                                    # NO duermen: van directo a bot-side.
    "tp_single_fallback": True,     # [H8] si NO cargan TP1+TP2 -> 1 sola TP
                                    # en Bitget que cierra el 100% en
                                    # tp1_pct (+2% = +20% PnL a 10x).
                                    # False = sin TP unico (bot-side).

    # -- [3.6c] Gate del Break Even --
    "be_after_tp1": True,           # [BE-TP1] el BE SOLO se evalua DESPUES
                                    # de ejecutarse el TP1

    # -- [3.7] Break Even (BE) --
    "be_trigger_pct": 0.0132,       # Activa BE cuando el precio esta +1.32% a favor
    "be_offset_pct": 0.002,         # Mueve el SL a +0.2% de la entrada (long) / -0.2% (short)

    # -- [3.8] Trailing Stop --
    "trailing_dist_pct": 0.0035,    # Distancia REAL del SL al pico (0.35%)
                                    # [FIX-4] antes era config muerto: ahora
                                    # el SL = pico * (1 -/+ 0.35%)
    "trailing_step_pct": 0.003,     # Mejora MINIMA del SL exigida antes de
                                    # enviar una nueva orden al exchange
    "trailing_enabled": False,      # [TRAIL-OFF] trailing APAGADO por
                                    # decision del usuario: la logica sigue
                                    # intacta en sl_tp.py [3]; para usarla
                                    # poner True (se activaria a 1:1 = +5%,
                                    # inalcanzable con TP2 en +3%)
                                    # (0.3% - proteccion anti rate-limit 429)

    # -- [3.9] Cooldown tras perdidas --
    "max_consecutive_losses": 3,    # Perdidas consecutivas antes de pausar
    "cooldown_hours": 4,            # Horas de pausa
    "cooldown_rolling_window": 6,   # Ventana de ultimos N trades para cooldown rolling
    "cooldown_loss_threshold": -0.30,  # Si PnL rolling < umbral, pausar

    # -- [3.10] Grafico enviado a Telegram --
    "chart_candles": 120,           # Velas visibles en la imagen
    "chart_dpi": 100,               # Resolucion del PNG
    "chart_dark_theme": True,       # Tema oscuro estilo TradingView
}


# =============================================================================
# [4] LOGGING - Reemplaza TODO print() (obligatorio para Render.com)
# =============================================================================
# Log FIJO en codigo (sin variables de entorno).
# En Render solo se usan estas 6 env vars:
#   BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE,
#   TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_URL
LOG_TO_FILE = True    # Fichero canalbot.log ademas de stdout
LOG_LEVEL = "INFO"    # Nivel: DEBUG/INFO/WARNING

_handlers = [logging.StreamHandler()]  # stdout (Render captura stdout/stderr)
if LOG_TO_FILE:
    # Fichero local .log (en Render el disco es efimero: solo para diagnostico)
    _log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canalbot.log")
    _handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
_log = logging.getLogger("canalbot")  # Logger unico del bot

# Alias corto usado por el resto del codigo
log = _log

# Definicion de flag() (requiere el logger creado arriba):
def flag(codigo: str, origen: str, mensaje: str, nivel: int = logging.WARNING):
    """[2.2] Emite un FLAG/WARNING estructurado (nunca usa print).
      Formato: [FLAG][ORIGEN:CODIGO] mensaje
      Ejemplos:
        [FLAG][CODIGO:CODIGO-001] ...   -> bug de este fichero
        [FLAG][BIBLIOTECA:429] ...      -> rate limit de ccxt/Bitget
        [FLAG][DATOS:DATOS-002] ...     -> datos invalidos del mercado
    """
    FLAG_COUNTS[codigo] = FLAG_COUNTS.get(codigo, 0) + 1
    _log.log(nivel, f"[FLAG][{origen}:{codigo}] {mensaje}")


def resumen_flags():
    """[2.4] Logea el recuento de flags emitidos (diagnostico de salud del bot)."""
    if not FLAG_COUNTS:
        log.info("[FLAGS] Sin incidencias registradas.")
        return
    total = sum(FLAG_COUNTS.values())
    top = sorted(FLAG_COUNTS.items(), key=lambda kv: kv[1], reverse=True)[:10]
    log.info(f"[FLAGS] Total={total} | Top: " +
             ", ".join(f"{k}x{v}" for k, v in top))


# Ahora si: redirigir warnings nativos de Python -> sistema de FLAGS
# (se hace aqui porque _showwarning_adaptado necesita que exista flag())
warnings.showwarning = _showwarning_adaptado


# Catalogo documentado de flags de CODIGO (bugs conocidos / limitaciones):
FLAGS_CODIGO_DOC = {
    "CODIGO-001": "Interpretacion del estado 'var' de Pine: se asume estado "
                  "propio por sitio de llamada (swing sz=50 / internal sz=5). "
                  "Si TradingView comparte estado, ajustar en seccion [9.3].",
    "CODIGO-002": "La estructura INTERNAL (sz=5) se calcula pero NO interviene "
                  "en el canal: displayStructure() solo se invoca con "
                  "internal=false (identico al Pine original).",
    "CODIGO-003": "Logica de trading de botbb_engine (detect_signal, "
                  "_scan_side_arrays, _check_divergence) EXCLUIDA por requisito: "
                  "solo se conservan sus matematicas en seccion [7].",
    "CODIGO-004": "Senales solo se alertan si la vela es la ACTUAL (evita "
                  "senales viejas de velas ya cerradas).",
    "CODIGO-005": "Deduplicacion de alertas por (symbol|side|timestamp de vela).",
    "CODIGO-006": "Excepcion inesperada dentro de evaluar_signalvwap().",
    "CODIGO-007": "Denominador de la regresion = 0 (no deberia ocurrir con n>=2).",
    "CODIGO-008": "Suma de residuos numerica negativa (error de punto flotante); "
                  "se trunca a 0.",
    "CODIGO-009": "SL calculado invalido (distancia <= 0 o > maximo).",
    "CODIGO-010": "No se pudieron registrar manejadores de senales del SO.",
    "CODIGO-011": "Senal de salida del SO recibida (SIGTERM de Render).",
    "CODIGO-012": "Sobreflujo potencial en math.exp() del canal (pendiente "
                  "extrema); se descarta el calculo de esa vela.",
    "CODIGO-013": "Extension del canal aproximada visualmente (matplotlib no "
                  "tiene Extend Left/Right de Pine; se extrapola la recta).",
    "CODIGO-014": "Ancla 'Earnings/Dividends/Splits' no aplica a crypto: se "
                  "ignora el reinicio de periodo (VWAP continuo).",
    "CODIGO-015": "(CORREGIDO) La desviacion del segmento dibujado usaba "
                  "sqrt(SSE)/(m-1) en vez de sqrt(SSE/(m-1)); la banda "
                  "dibujada salia ~sqrt(m-1) mas estrecha que la real.",
    "CODIGO-016": "La leyenda 'SignalVWAP Clean' no muestra valores de plots "
                  "(TradingView muestra '0.000 0.000'): diferencia cosmética.",
    # CODIGO-017 ELIMINADO [TPO-2]: el TP estructural (pivote/borde/rr_ratio)
    # ya no existe; ahora son TP1/TP2 porcentuales (sl_tp.tp_accion).
}

# =============================================================================
# [5] BLACKLIST - Activos NO crypto en Bitget (acciones, ETFs, commodities)
#      (extraido literalmente de botbb_engine.py)
# =============================================================================
NON_CRYPTO_BASES: set = {
    # --- Acciones US ---
    "AAL", "AAOI", "AAPL", "AAPU", "ABNB", "ACHR", "ADBE", "ADI", "ADVANTEST", "AEHR", "ALAB",
    "AMAT", "AMC", "AMD", "AMGN", "AMKR", "AMZN", "AMZU", "ANET", "APD", "APLD",
    "APP", "APR", "ARM", "ARQQ", "ARX", "ASML", "ASTS", "AVGO", "AXTI",
    "BA", "BABA", "BAC", "BB", "BBSTOCK", "BEAT", "BILL", "BITO", "BKNG", "BMNR",
    "BREV", "BRKB", "BSB", "BZ",
    "C", "CAT", "CBRS", "CCL", "CGNX", "CHIP", "CIEN", "CL", "CMCSA", "COHR", "CONL",
    "COIN", "COP", "COST", "CPNG", "CRCL", "CRDO", "CRM", "CRWD", "CRWV", "CSCO", "CVX", "CXMT",
    "DASH", "DDOG", "DE", "DELL", "DKNG",
    "F", "FDX",
    "GE", "GM", "GME", "GOOGL", "GS",
    "HD", "HIMS", "HOOD", "HPE", "HPQ",
    "INTC",
    "JPM",
    "KO",
    "LMT", "LOW",
    "MA", "MARA", "MCD", "META", "MRNA", "MRVL", "MSFT", "MU",
    "NFLX", "NKE", "NOC", "NOW", "NTNX", "NVDA", "NVAX", "NVO", "NXPI",
    "O", "OKTA", "ON", "ORCL",
    "PARA", "PATH", "PDD", "PEP", "PLTR", "PYPL",
    "QCOM", "QQQ",
    "RBLX", "RIVN", "ROKU", "RTX",
    "S", "SOFI", "SPOT", "SQ", "SPX", "SP500",
    "T", "TOST", "TSLA", "TTD", "TTWO", "TXN",
    "UBER", "UNH",
    "V", "VIPS", "VST",
    "W", "WBA", "WFC", "WMT",
    "XPEV", "ZS",
    # --- ETFs ---
    "IWM",
    # --- Commodities ---
    "COPPER", "NATGAS",
}


# =============================================================================
# [6] VARIABLES DE ENTORNO (Render.com Dashboard -> Environment)
#      NUNCA se escriben credenciales en el codigo.
# =============================================================================
API_KEY = os.environ.get("BITGET_API_KEY", "")        # API Key de Bitget
SECRET_KEY = os.environ.get("BITGET_SECRET_KEY", "")  # Secret de Bitget
PASSPHRASE = os.environ.get("BITGET_PASSPHRASE", "")  # Passphrase de Bitget
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")       # Token del bot de Telegram
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")   # Chat ID de destino

# Validacion temprana de credenciales (solo es obligatorio para trading real;
# el escaneo de senales + graficos funciona sin API keys: endpoints publicos)
if not API_KEY or not SECRET_KEY or not PASSPHRASE:
    _log.warning(
        "[ENV] BITGET_API_KEY/SECRET_KEY/PASSPHRASE no definidos. "
        "El escaneo publico (TOP100 + OHLCV) funcionara; las operaciones "
        "reales quedan DESHABILITADAS."
    )
if not TG_TOKEN or not TG_CHAT_ID:
    _log.warning("[ENV] TELEGRAM_TOKEN/TELEGRAM_CHAT_ID no definidos: no se enviaran imagenes.")


# =============================================================================
# [7] CALCULOS MATEMATICOS PRESERVADOS de botbb_engine.py
#      >>> ESTOS INDICADORES NO SE APLICAN COMO ESTRATEGIA EN ESTE BOT.
#      Se conservan integros y comentados para futuras aplicaciones
#      (requisito: conservar TODOS los calculos matematicos de la logica
#       de trading original, pero JAMAS ejecutar su logica de senales).
# =============================================================================
class MathPreservados:
    """
    [7.0] Contenedor de las matematicas puras extraidas de botbb_engine.
      - Son funciones SIN estado (salvo calculate_* que leen self.cfg).
      - La logica de trading que las consumia (detect_signal, _scan_side_arrays,
        _check_divergence) ha sido EXCLUIDA (flag CODIGO-003).
    """

    # -----------------------------------------------------------------
    # [7.1] HEIKIN ASHI - Convierte OHLCV normal a velas Heikin Ashi
    # -----------------------------------------------------------------
    @staticmethod
    def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
        """
        [7.1] Heikin Ashi (vectorizado con numpy).
          ha_close = (open + high + low + close) / 4
          ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2   (recursivo)
          ha_high   = max(high, ha_open, ha_close)
          ha_low    = min(low,  ha_open, ha_close)
        NOTA: el calculo recursivo de ha_open es O(n); para n<=500 se usa
        una forma cerrada con potencias de 0.5 (misma suma que el bucle).
        """
        df = df.copy()
        o = df["open"].values.astype(np.float64)
        h = df["high"].values.astype(np.float64)
        l = df["low"].values.astype(np.float64)
        c = df["close"].values.astype(np.float64)

        ha_close = (o + h + l + c) * 0.25
        n = len(df)

        if n == 0:  # Proteccion: dataframe vacio
            df["ha_close"] = ha_close
            df["ha_open"] = np.float64(0.0)
            df["ha_high"] = h
            df["ha_low"] = l
            return df

        init = (o[0] + c[0]) * 0.5

        if n <= 500:
            # Forma cerrada: ha_open[i] = 0.5^i * init + suma_{j<i} 0.5^(i-1-j) * ha_close[j]
            powers_2 = np.power(2.0, np.arange(n, dtype=np.float64))
            decay = 0.5 ** np.arange(n, dtype=np.float64)
            hc_weighted = ha_close * powers_2
            cum_hcw = np.cumsum(hc_weighted)
            ha_open = np.empty(n, dtype=np.float64)
            ha_open[0] = init
            ha_open[1:] = decay[1:] * (init + cum_hcw[:-1])
        else:
            # Bucle directo (identico a la definicion recursiva)
            ha_open = np.empty(n, dtype=np.float64)
            ha_open[0] = init
            half = np.float64(0.5)
            for i in range(1, n):
                ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) * half

        ha_high = np.maximum(np.maximum(h, ha_open), ha_close)
        ha_low = np.minimum(np.minimum(l, ha_open), ha_close)

        df["ha_close"] = ha_close
        df["ha_open"] = ha_open
        df["ha_high"] = ha_high
        df["ha_low"] = ha_low
        return df

    # -----------------------------------------------------------------
    # [7.2] BOLLINGER BANDS - Bandas de Bollinger (length + desviaciones)
    # -----------------------------------------------------------------
    def calculate_bb(self, close: pd.Series, cfg: dict):
        """
        [7.2] Bollinger Bands. Retorna (upper, basis, lower).
          basis = SMA(close, length)
          upper = basis + mult * stddev(close, length)
          lower = basis - mult * stddev(close, length)
        """
        length = cfg["bb_length"]
        mult = cfg["bb_mult"]
        basis = close.rolling(length).mean()
        dev = mult * close.rolling(length).std()
        upper = basis + dev
        lower = basis - dev
        return upper, basis, lower

    # -----------------------------------------------------------------
    # [7.3] EMA estilo TradingView (inicializacion con SMA) - REUTILIZADA
    #       por SignalVWAP (seccion [9.1]) porque replica ta.ema de Pine.
    # -----------------------------------------------------------------
    @staticmethod
    def ema_tv(close: pd.Series, period: int) -> pd.Series:
        """
        [7.3] EMA con inicializacion SMA (identica a ta.ema de TradingView):
          EMA[period-1] = SMA(close, period)
          EMA[i] = close[i] * 2/(period+1) + EMA[i-1] * (1 - 2/(period+1))
        Devuelve NaN mientras no haya 'period' velas.
        """
        result = pd.Series(np.nan, index=close.index, dtype=float)
        if len(close) < period:
            return result
        result.iloc[period - 1] = close.iloc[:period].mean()
        alpha = 2.0 / (period + 1)
        for i in range(period, len(close)):
            result.iloc[i] = close.iloc[i] * alpha + result.iloc[i - 1] * (1.0 - alpha)
        return result

    # -----------------------------------------------------------------
    # [7.4] MACD - Linea de senal y overlay booleano
    # -----------------------------------------------------------------
    def calculate_macd_overlay(self, close: pd.Series, cfg: dict) -> pd.Series:
        """
        [7.4a] MACD overlay: Serie booleana, True = MACD >= Signal (verde).
          macd   = EMA(fast) - EMA(slow)
          signal = SMA(macd, signal_length)
        """
        fast = self.ema_tv(close, cfg["macd_fast"])
        slow = self.ema_tv(close, cfg["macd_slow"])
        macd = fast - slow
        signal = macd.rolling(cfg["macd_signal"]).mean()
        return macd >= signal

    def calculate_signal_line(self, close: pd.Series, cfg: dict) -> pd.Series:
        """
        [7.4b] Signal line estetica de botbb: close + SMA(MACD, 9)
        (se dibujaba sobre el precio junto a las velas HA).
        """
        fast = self.ema_tv(close, cfg["macd_fast"])
        slow = self.ema_tv(close, cfg["macd_slow"])
        macd = fast - slow
        signal_val = macd.rolling(cfg["macd_signal"]).mean()
        return close + signal_val

    # -----------------------------------------------------------------
    # [7.5] VWAP SEMANAL de botbb (anclado a semana ISO) - NO usado aqui;
    #       SignalVWAP usa su propio VWAP con ancla configurable [9.2].
    # -----------------------------------------------------------------
    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> pd.Series:
        """
        [7.5] VWAP anclado SEMANALMENTE (reinicia cada lunes 00:00 UTC):
          VWAP = cumsum(hlc3 * volume) / cumsum(volume)  por semana ISO
        """
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        close = df["close"].values.astype(np.float64)
        volume = df["volume"].values.astype(np.float64)

        typical_price = (high + low + close) / 3.0
        tp_vol = typical_price * volume

        dates = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        iso = dates.dt.isocalendar()
        week_keys = (iso.year.astype(str) + iso.week.astype(str)).values

        n = len(df)
        vwap = np.empty(n, dtype=np.float64)
        cum_tp_vol = 0.0
        cum_vol = 0.0

        for i in range(n):
            if i == 0 or week_keys[i] != week_keys[i - 1]:
                cum_tp_vol = tp_vol[i]   # Nueva semana: reiniciar acumuladores
                cum_vol = volume[i]
            else:
                cum_tp_vol += tp_vol[i]
                cum_vol += volume[i]
            vwap[i] = cum_tp_vol / cum_vol if cum_vol > 0 else np.nan

        return pd.Series(vwap, index=df.index)

    # -----------------------------------------------------------------
    # [7.6] RSI (Wilder's RMA) - identico a ta.rsi de TradingView
    # -----------------------------------------------------------------
    def calculate_rsi(self, close: pd.Series, cfg: dict) -> pd.Series:
        """
        [7.6] RSI de Wilder:
          diff[i]   = close[i] - close[i-1]
          up/down   = max/min(diff, 0) suavizados con RMA(alpha=1/length)
          RSI       = 100 - 100/(1 + up/down)
        """
        length = cfg["rsi_length"]
        src = close.values.astype(np.float64)
        n = len(src)
        rsi = np.full(n, np.nan, dtype=np.float64)

        if n < length + 1:
            return pd.Series(rsi, index=close.index)

        diff = np.zeros(n, dtype=np.float64)
        for i in range(1, n):
            if np.isfinite(src[i]) and np.isfinite(src[i - 1]):
                diff[i] = src[i] - src[i - 1]

        up_raw = np.where(diff > 0, diff, 0.0)
        down_raw = np.where(diff < 0, -diff, 0.0)

        alpha = 1.0 / length  # Wilder's RMA = EMA con alpha = 1/length
        up = np.empty(n, dtype=np.float64)
        down = np.empty(n, dtype=np.float64)

        up[length] = np.mean(up_raw[1:length + 1])
        down[length] = np.mean(down_raw[1:length + 1])

        for i in range(length + 1, n):
            up[i] = up[i - 1] * (1.0 - alpha) + up_raw[i] * alpha
            down[i] = down[i - 1] * (1.0 - alpha) + down_raw[i] * alpha

        for i in range(length, n):
            u = up[i] if np.isfinite(up[i]) else 0.0
            d = down[i] if np.isfinite(down[i]) else 0.0
            if d == 0.0:
                rsi[i] = 100.0 if u != 0.0 else 50.0
            elif u == 0.0:
                rsi[i] = 0.0
            else:
                rsi[i] = 100.0 - (100.0 / (1.0 + u / d))

        return pd.Series(rsi, index=close.index)

    # -----------------------------------------------------------------
    # [7.7] STOCHRSI - identico a TradingView
    # -----------------------------------------------------------------
    def calculate_stochrsi(self, close: pd.Series, cfg: dict):
        """
        [7.7] StochRSI:
          stoch = (RSI - min(RSI, stoch_len)) / (max - min) * 100
          K = SMA(stoch, smooth_k);  D = SMA(K, smooth_d)
        Retorna: (K_series, D_series)
        """
        rsi_vals = self.calculate_rsi(close, cfg).values.astype(np.float64)
        stoch_len = cfg["stoch_length"]
        sk = cfg["smooth_k"]
        sd = cfg["smooth_d"]
        n = len(rsi_vals)

        stoch_raw = np.full(n, np.nan, dtype=np.float64)
        for i in range(stoch_len - 1, n):
            window = rsi_vals[i - stoch_len + 1: i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) < 2:
                continue
            lo = np.min(valid)
            hi = np.max(valid)
            if hi - lo > 1e-10:
                stoch_raw[i] = (rsi_vals[i] - lo) / (hi - lo) * 100.0
            else:
                stoch_raw[i] = 50.0  # RSI plano -> valor neutro

        k = pd.Series(stoch_raw, index=close.index).rolling(sk).mean().values
        d = pd.Series(k, index=close.index).rolling(sd).mean().values
        return pd.Series(k, index=close.index), pd.Series(d, index=close.index)

    # -----------------------------------------------------------------
    # [7.8] DIVERGENCIA REGULAR - deteccion con pivotes y filtros
    # -----------------------------------------------------------------
    @staticmethod
    def detect_divergence(price: np.ndarray, indicator: np.ndarray,
                          pivot_left: int = 2, pivot_right: int = 2,
                          min_swing_pct: float = 0.003,
                          min_pivot_distance: int = 3,
                          min_indicator_diff: float = 4.0):
        """
        [7.8] Detecta divergencia REGULAR bullish/bearish con pivotes.
          BEAR: precio higher-high + indicador lower-high.
          BULL: precio lower-low  + indicador higher-low.
          Filtros: min_swing_pct (swing minimo del precio),
                   min_pivot_distance (velas minimas entre pivotes),
                   min_indicator_diff (diferencia minima del indicador).
        Retorna tupla (tipo, idx, precio, indicador, ...) o None.
        """
        n = len(price)
        ph = []  # pivotes altos: (indice, precio, indicador)
        pl = []  # pivotes bajos

        for i in range(pivot_left, n - pivot_right):
            if np.isnan(price[i]) or np.isnan(indicator[i]):
                continue

            # Pivot High: maximo local en la ventana [i-pl, i+pr]
            is_high = True
            for j in range(i - pivot_left, i + pivot_right + 1):
                if j == i or np.isnan(price[j]):
                    continue
                if price[j] >= price[i]:
                    is_high = False
                    break
            if is_high:
                ph.append((i, price[i], indicator[i]))

            # Pivot Low: minimo local en la ventana
            is_low = True
            for j in range(i - pivot_left, i + pivot_right + 1):
                if j == i or np.isnan(price[j]):
                    continue
                if price[j] <= price[i]:
                    is_low = False
                    break
            if is_low:
                pl.append((i, price[i], indicator[i]))

        # BEARISH: precio sube (higher high) pero el indicador baja
        if len(ph) >= 2:
            newest_idx, newest_p, newest_i = ph[-1]
            for k in range(len(ph) - 2, -1, -1):
                older_idx, older_p, older_i = ph[k]
                if abs(newest_idx - older_idx) < min_pivot_distance:
                    continue  # Filtro 1: distancia minima entre pivotes
                avg_price = (newest_p + older_p) / 2.0
                if avg_price <= 0:
                    continue
                swing_pct = abs(newest_p - older_p) / avg_price
                if swing_pct < min_swing_pct:
                    continue  # Filtro 2: swing minimo del precio
                if abs(newest_i - older_i) < min_indicator_diff:
                    continue  # Filtro 3: diferencia minima del indicador
                if newest_p > older_p and newest_i < older_i:
                    return ('bear', newest_idx, newest_p, newest_i,
                            older_idx, older_p, older_i)

        # BULLISH: precio baja (lower low) pero el indicador sube
        if len(pl) >= 2:
            newest_idx, newest_p, newest_i = pl[-1]
            for k in range(len(pl) - 2, -1, -1):
                older_idx, older_p, older_i = pl[k]
                if abs(newest_idx - older_idx) < min_pivot_distance:
                    continue
                avg_price = (newest_p + older_p) / 2.0
                if avg_price <= 0:
                    continue
                swing_pct = abs(newest_p - older_p) / avg_price
                if swing_pct < min_swing_pct:
                    continue
                if abs(newest_i - older_i) < min_indicator_diff:
                    continue
                if newest_p < older_p and newest_i > older_i:
                    return ('bull', newest_idx, newest_p, newest_i,
                            older_idx, older_p, older_i)

        return None


# Config extra de los indicadores PRESERVADOS (no afectan a SignalVWAP)
MATH_CFG = {
    "bb_length": 20, "bb_mult": 2.0,               # [7.2] Bollinger
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,  # [7.4] MACD
    "rsi_length": 14,                                # [7.6] RSI
    "stoch_length": 14, "smooth_k": 3, "smooth_d": 3,    # [7.7] StochRSI
    "doji_threshold": 0.10,                          # umbral doji (logica excluida)
    "confirmation_window": 8,                        # ventana de confirmacion (excluida)
}

# Instancia unica de matematicas preservadas
_math = MathPreservados()

# =============================================================================
# [8] MOTOR BITGET / TELEGRAM / RENDER - extraido de botbb_engine.py
#      Todo comentado en espanol. La logica de trading de botbb NO se copia;
#      solo la capa de infraestructura (conexion, ordenes, gestion, alertas).
# =============================================================================
class CanalBot:
    """
    [8.0] Motor asincrono para Bitget:
      [8.1] Ciclo de vida y conexion        (start/stop/_connect)
      [8.2] Wrappers ccxt sync->async        (_exch_call)
      [8.3] Balance y TOP 100 por volumen    (get_balance/get_top_symbols)
      [8.4] Descarga batch de velas OHLCV    (fetch_ohlcv_batch)
      [8.5] Telegram: texto y fotos          (send_telegram/_photo)
      [8.6] Ordenes: abrir/gestionar/cerrar  (open/manage/close_position)
      [8.7] Persistencia CSV/JSON            (_save/_load_*)
      [8.8] Utilidades y cooldown            (can_open/record_trade_result)
    """

    __slots__ = (
        "cfg", "exchange", "semaphore", "_aio_session",
        "alerts_history", "peak_prices", "cooldowns", "session_active",
        "trade_entries", "trail_counts", "adverse_prices",
        "consecutive_losses", "cooldown_until", "last_scan_time", "rolling_pnl",
        "trades_csv", "trade_entries_path", "TRADE_CSV_HEADERS",
        "alertas_enviadas", "_tp_recon_last",
    )

    def __init__(self, config: dict = None):
        """[8.1a] Constructor: config + estado en memoria + rutas de archivos."""
        self.cfg = {**CONFIG, **(config or {})}
        self.exchange = None
        # Semaforo: limite global de llamadas concurrentes a la API (anti-429)
        self.semaphore = asyncio.Semaphore(self.cfg["max_concurrent_fetches"])
        self._aio_session: Optional[aiohttp.ClientSession] = None

        # --- Estado de sesion (memoria RAM, se regenera al reiniciar) ---
        self.alerts_history: dict = {}     # flags BE/trailing por simbolo
        self.peak_prices: dict = {}        # max/min alcanzado por posicion
        self.cooldowns: dict = {}          # cooldown por simbolo (1h tras cierre)
        self.session_active: set = set()   # simbolos con posicion abierta
        self.trade_entries: dict = {}      # entradas abiertas (persistidas a JSON)
        self.trail_counts: dict = {}       # nº de movimientos de trailing
        self.adverse_prices: dict = {}     # peor precio visto (max adverse)
        self.alertas_enviadas: dict = {}   # [CODIGO-005] dedupe de alertas
        self._tp_recon_last: dict = {}     # [TPO-3] throttle reconcile TP

        # --- Cooldown global por perdidas consecutivas ---
        self.consecutive_losses: int = 0
        self.cooldown_until: Optional[float] = None
        self.last_scan_time: float = 0.0
        self.rolling_pnl: list = []        # PnL de los ultimos N trades

        # --- Archivos de persistencia (junto al script) ---
        base = os.path.dirname(os.path.abspath(__file__))
        self.trades_csv = os.path.join(base, "trades.csv")
        self.trade_entries_path = os.path.join(base, "trade_entries.json")

        # Cabeceras del CSV de trades (para futuros analisis/post-mortem)
        self.TRADE_CSV_HEADERS = [
            "entry_time", "exit_time", "symbol", "side", "entry_price", "exit_price",
            "sl_price", "tp_price", "sl_pct", "tp_pct", "quantity",
            "balance_before", "balance_after", "pnl", "fees", "net_pnl",
            "status", "duration_hours", "close_reason",
            "be_triggered", "be_price", "trail_count", "trail_peak_price", "trail_final_sl",
            "entry_weekday", "entry_hour", "size_usdt", "risk_pct",
            "max_favorable_pct", "max_adverse_pct",
        ]

    # -----------------------------------------------------------------
    # [8.1] CICLO DE VIDA: arranque, parada y conexion a Bitget
    # -----------------------------------------------------------------
    async def start(self) -> bool:
        """[8.1b] Crea la sesion HTTP (Telegram) y conecta a Bitget."""
        self._aio_session = aiohttp.ClientSession()
        return await self._connect()

    async def stop(self):
        """[8.1c] Cierra exchange y sesiones HTTP de forma ordenada."""
        if self.exchange:
            try:
                await self.exchange.close()  # cierra el pool HTTP de ccxt
            except Exception as e:
                flag("CLOSE-EXCH", origen_de_excepcion(e),
                     f"Error cerrando exchange: {e}", logging.DEBUG)
            self.exchange = None
        if self._aio_session:
            await self._aio_session.close()
            self._aio_session = None
        log.info("canalBot detenido y conexiones cerradas.")
        resumen_flags()  # [2.4] resumen de salud al salir

    async def _connect(self) -> bool:
        """
        [8.1d] Conexion sincrona a Bitget ejecutada en un thread (to_thread)
        para NO bloquear el event loop async. Rate limit -> reintento en 5s.
        """
        def _sync():
            exch = ccxt.bitget({
                "apiKey": API_KEY,
                "secret": SECRET_KEY,
                "password": PASSPHRASE,
                "enableRateLimit": True,                 # ccxt gestiona 429
                "options": {"defaultType": "swap"},      # futuros perpetuos
            })
            exch.load_markets()                          # cache de mercados
            return exch

        try:
            self.exchange = await asyncio.to_thread(_sync)
            log.info("Conexion exitosa a Bitget.")
            await self._load_trade_entries()
            return True
        except AuthenticationError as e:
            flag("AUTH", ORIGEN_BIBLIOTECA, f"Credenciales invalidas: {e}", logging.CRITICAL)
            return False
        except PermissionDenied as e:
            flag("PERM", ORIGEN_BIBLIOTECA, f"Sin permisos: {e}", logging.CRITICAL)
            return False
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA, "Rate limit al conectar. Reintento en 5s.",
                 logging.WARNING)
            await asyncio.sleep(5)
            return await self._connect()                 # reintento recursivo
        except (NetworkError, RequestTimeout) as e:
            flag("NET", ORIGEN_BIBLIOTECA, f"Error de red al conectar: {e}", logging.WARNING)
            return False
        except ExchangeNotAvailable as e:
            flag("503", ORIGEN_BIBLIOTECA, f"Bitget no disponible: {e}", logging.WARNING)
            return False
        except Exception as e:
            flag("CONN-UNEXPECTED", origen_de_excepcion(e),
                 f"Error de conexion inesperado: {e}", logging.CRITICAL)
            return False

    # -----------------------------------------------------------------
    # [8.2] WRAPPERS ccxt sync -> async (con semaforo anti-sobrecarga)
    # -----------------------------------------------------------------
    async def _exch_call(self, method: str, *args, **kwargs):
        """
        [8.2a] Ejecuta un metodo ccxt SINCRONO en el thread-pool.
        Usa semaforo para limitar concurrencia (evita 429 de Bitget).
        """
        async with self.semaphore:
            fn = getattr(self.exchange, method)
            return await asyncio.to_thread(fn, *args, **kwargs)

    # -----------------------------------------------------------------
    # [8.3] BALANCE y TOP SIMBOLOS por volumen 24h
    # -----------------------------------------------------------------
    async def get_balance(self) -> float:
        """[8.3a] Balance total en USDT. Devuelve 0.0 en cualquier error
        (nunca lanza excepcion: el bucle principal no debe caerse)."""
        try:
            data = await self._exch_call("fetch_balance")
            return float(data["total"].get("USDT", 0))
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA, "get_balance: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return 0.0
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA, "get_balance: error de red.", logging.WARNING)
            return 0.0
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"get_balance: {e}", logging.ERROR)
            return 0.0
        except Exception as e:
            flag("BAL-UNEXPECTED", origen_de_excepcion(e),
                 f"get_balance: {e}", logging.ERROR)
            return 0.0

    async def get_top_symbols(self, n: int = 100) -> list:
        """
        [8.3b] TOP N permutativos USDT:USDT por volumen de las ultimas 24h,
        excluyendo la BLACKLIST de activos no-crypto (seccion [5]).
        """
        try:
            tickers = await self._exch_call("fetch_tickers")
            ranked = [
                (s, float(t.get("quoteVolume", 0)))
                for s, t in tickers.items()
                if s.endswith("/USDT:USDT") and s.split("/")[0] not in NON_CRYPTO_BASES
            ]
            ranked.sort(key=lambda x: x[1], reverse=True)
            log.info(f"TOP symbols: {len(ranked)} cryptos tras blacklist "
                     f"({len(NON_CRYPTO_BASES)} excluidos)")
            return [s for s, _ in ranked[:n]]
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA, "get_top_symbols: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return []
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA, "get_top_symbols: error de red.", logging.WARNING)
            return []
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"get_top_symbols: {e}", logging.ERROR)
            return []
        except Exception as e:
            flag("TOP-UNEXPECTED", origen_de_excepcion(e),
                 f"get_top_symbols: {e}", logging.ERROR)
            return []

    # -----------------------------------------------------------------
    # [8.4] DESCARGA DE VELAS OHLCV (asincrona, concurrencia controlada)
    # -----------------------------------------------------------------
    async def _fetch_single(self, exch, symbol: str, timeframe: str, limit: int):
        """[8.4a] Descarga OHLCV de UN simbolo. Devuelve (symbol, velas|None).
        Nunca lanza: en error devuelve None y el resto sigue."""
        async with self.semaphore:
            try:
                ohlcv = await exch.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                return symbol, ohlcv
            except RateLimitExceeded:
                flag("429", ORIGEN_BIBLIOTECA,
                     f"fetch_ohlcv {symbol}: rate limit, espero 2s.", logging.WARNING)
                await asyncio.sleep(2)
                return symbol, None
            except (NetworkError, RequestTimeout):
                flag("NET", ORIGEN_BIBLIOTECA,
                     f"fetch_ohlcv {symbol}: error de red.", logging.WARNING)
                await asyncio.sleep(1)
                return symbol, None
            except Exception as e:
                flag("OHLCV-UNEXPECTED", origen_de_excepcion(e),
                     f"fetch_ohlcv {symbol}: {e}", logging.ERROR)
                return symbol, None

    async def fetch_ohlcv_batch(self, symbols: list, timeframe: str = "5m",
                                limit: int = 100) -> dict:
        """
        [8.4b] Descarga BATCH de velas con un cliente async temporal.
        Devuelve {symbol: [[ts,o,h,l,c,v], ...]} solo con los que respondieron.
        """
        exch = ccxt_async.bitget({
            "apiKey": API_KEY,
            "secret": SECRET_KEY,
            "password": PASSPHRASE,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            tasks = [self._fetch_single(exch, s, timeframe, limit) for s in symbols]
            results = await asyncio.gather(*tasks)
            return {r[0]: r[1] for r in results if r[1] is not None}
        finally:
            await exch.close()  # liberar sockets siempre

    # -----------------------------------------------------------------
    # [8.5] TELEGRAM - texto y fotos (aiohttp, con timeouts)
    # -----------------------------------------------------------------
    async def send_telegram(self, message: str):
        """[8.5a] Envia mensaje de TEXTO a Telegram. Timeout 10s, sin excepciones."""
        if not TG_TOKEN or not TG_CHAT_ID:
            flag("TG-ENV", ORIGEN_DATOS, "Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.",
                 logging.WARNING)
            return
        if not self._aio_session:
            flag("TG-SESSION", ORIGEN_CODIGO,
                 "Sesion aiohttp no inicializada en send_telegram.", logging.WARNING)
            return
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            async with self._aio_session.post(
                url,
                data={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    flag(f"TG-HTTP-{resp.status}", ORIGEN_BIBLIOTECA,
                         f"Telegram sendMessage: {body[:200]}", logging.WARNING)
        except asyncio.TimeoutError:
            flag("TG-TIMEOUT", ORIGEN_BIBLIOTECA, "Timeout 10s enviando mensaje.",
                 logging.WARNING)
        except Exception as e:
            flag("TG-UNEXPECTED", origen_de_excepcion(e),
                 f"send_telegram: {type(e).__name__}: {e}", logging.WARNING)

    async def send_telegram_photo(self, buf: BytesIO, caption: str = "") -> bool:
        """
        [8.5b] Envia FOTO (PNG) a Telegram con pie de foto.
        La imagen es el grafico estilo TradingView generado en [10A].
        Timeout 60s. Devuelve True solo si HTTP 200.
        """
        if not TG_TOKEN or not TG_CHAT_ID:
            flag("TG-ENV", ORIGEN_DATOS,
                 "Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en variables de entorno.",
                 logging.WARNING)
            return False
        if not self._aio_session:
            flag("TG-SESSION", ORIGEN_CODIGO,
                 "Sesion aiohttp no inicializada; no se puede enviar foto.",
                 logging.WARNING)
            return False
        if not buf:
            flag("TG-BUF", ORIGEN_CODIGO, "Buffer de imagen vacio.", logging.WARNING)
            return False
        try:
            buf.seek(0)
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            form = aiohttp.FormData()
            form.add_field("chat_id", TG_CHAT_ID)
            if caption:
                form.add_field("caption", caption[:1024])  # limite Telegram
                form.add_field("parse_mode", "Markdown")
            form.add_field("photo", buf.read(),
                           filename="chart.png", content_type="image/png")
            async with self._aio_session.post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    log.info("[TG] Grafico enviado a Telegram.")
                    return True
                body = await resp.text()
                flag(f"TG-HTTP-{resp.status}", ORIGEN_BIBLIOTECA,
                     f"Telegram sendPhoto: {body[:200]}", logging.WARNING)
                return False
        except asyncio.TimeoutError:
            flag("TG-TIMEOUT", ORIGEN_BIBLIOTECA, "Timeout 60s enviando foto.",
                 logging.WARNING)
            return False
        except Exception as e:
            flag("TG-UNEXPECTED", origen_de_excepcion(e),
                 f"send_telegram_photo: {type(e).__name__}: {e}", logging.WARNING)
            return False

    # -----------------------------------------------------------------
    # [8.6] ORDENES: abrir, gestionar (BE/trailing) y cerrar posiciones
    # -----------------------------------------------------------------
    async def _update_stop_loss(self, symbol: str, side: str, new_sl: float) -> bool:
        """
        [8.6a] Actualiza el STOP LOSS de una posicion en Bitget via el
        endpoint de TP/SL posicional (pos_loss). Devuelve True si OK.
        """
        try:
            new_sl_fmt = await self._exch_call("price_to_precision", symbol, new_sl)
            clean_symbol = symbol.split(":")[0].replace("/", "")
            params = {
                "symbol": clean_symbol,
                "marginCoin": "USDT",
                "productType": "USDT-FUTURES",
                "planType": "pos_loss",
                "stopLossTriggerPrice": str(new_sl_fmt),
                "stopLossTriggerType": "fill_price",
                "holdSide": "long" if side == "long" else "short",
            }
            await self._exch_call("private_mix_post_v2_mix_order_place_pos_tpsl", params)
            return True
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_update_stop_loss {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA, f"_update_stop_loss {symbol}: {e}", logging.ERROR)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_update_stop_loss {symbol}: error de red.", logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"_update_stop_loss {symbol}: {e}", logging.ERROR)
            return False
        except Exception as e:
            flag("SL-UNEXPECTED", origen_de_excepcion(e),
                 f"Actualizando SL {symbol}: {e}", logging.ERROR)
            return False

    # -----------------------------------------------------------------
    # [8.6b-ter] TP EN EL EXCHANGE [TPO-3]: ordenes profit_plan Bitget
    #   El TP1/TP2 se colocan como ordenes VISIBLES en el panel de la
    #   posicion de Bitget y las ejecuta el exchange (no el bot).
    #   Endpoints verificados V2 classic (ccxt implicit):
    #     place-tpsl-order (planType=profit_plan, size, triggerPrice)
    #     orders-plan-pending / orders-plan-history (reconcile)
    #     cancel-plan-order (limpieza al cerrar)
    # -----------------------------------------------------------------
    @staticmethod
    def _bsymbol(symbol: str) -> str:
        """[TPO-3] 'BTC/USDT:USDT' -> 'BTCUSDT' (formato API Bitget)."""
        return symbol.split(":")[0].replace("/", "")

    @staticmethod
    def _plan_list(body) -> list:
        """[TPO-3] Normaliza la respuesta de los endpoints de planes
        (acepta {'data': {'entrustedList': [...]}} o lista directa)."""
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, dict):
            lst = data.get("entrustedList", [])
            return lst if isinstance(lst, list) else []
        return data if isinstance(data, list) else []

    async def _place_tp_order(self, symbol: str, side: str,
                              trigger_price: float, qty: float,
                              tag: str) -> bool:
        """[TPO-3a] Coloca UNA orden profit_plan (TP) en Bitget. Devuelve
        True solo si Bitget la acepto. Nunca lanza excepcion."""
        try:
            market = await self._exch_call("market", symbol)
            min_notional = market.get("limits", {}).get("cost", {}).get("min") or 5.0
            if qty <= 0 or not math.isfinite(qty * trigger_price) \
                    or qty * trigger_price < min_notional:
                # [H6] ORIGEN_CODIGO: la causa raiz es NUESTRO sizing
                # (margen/step -> qty demasiado pequena), no la libreria.
                flag("TP-NOTIONAL-MIN", ORIGEN_CODIGO,
                     f"{symbol} {tag}: qty={qty} notional="
                     f"{qty * trigger_price:.2f} < min {min_notional} USDT; "
                     f"orden TP omitida.", logging.WARNING)
                return False
            fmt_trig = await self._exch_call("price_to_precision", symbol,
                                             trigger_price)
            fmt_qty = await self._exch_call("amount_to_precision", symbol, qty)
            params = {
                "symbol": self._bsymbol(symbol),
                "marginCoin": "USDT",
                "productType": "USDT-FUTURES",
                "planType": "profit_plan",
                "triggerPrice": str(fmt_trig),   # executePrice ausente -> mercado
                "triggerType": "fill_price",
                "holdSide": "long" if side == "long" else "short",
                "size": str(fmt_qty),
                "clientOid": f"{tag}{int(time.time() * 1000)}",
            }
            await self._exch_call(
                "private_mix_post_v2_mix_order_place_tpsl_order", params)
            log.info(f"{symbol} TP {tag} colocado en Bitget: "
                     f"trigger={fmt_trig} size={fmt_qty}")
            return True
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_place_tp_order {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA, f"_place_tp_order {symbol}: {e}",
                 logging.ERROR)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_place_tp_order {symbol}: error de red.", logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"_place_tp_order {symbol}: {e}",
                 logging.ERROR)
            return False
        except Exception as e:
            flag("TPPLACE-UNEXPECTED", origen_de_excepcion(e),
                 f"_place_tp_order {symbol}: {e}", logging.ERROR)
            return False

    async def _cancel_exchange_tps(self, symbol: str) -> bool:
        """[TPO-3b] Cancela TODAS las ordenes profit_plan del simbolo
        (idempotente, best-effort: un fallo solo deja flag)."""
        try:
            params = {
                "productType": "USDT-FUTURES",
                "symbol": self._bsymbol(symbol),
                "marginCoin": "USDT",
                "planType": "profit_plan",
            }
            await self._exch_call(
                "private_mix_post_v2_mix_order_cancel_plan_order", params)
            log.debug(f"{symbol}: profit_plan pendientes cancelados.")
            return True
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_cancel_exchange_tps {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA,
                 f"_cancel_exchange_tps {symbol}: {e}", logging.WARNING)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_cancel_exchange_tps {symbol}: error de red.",
                 logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA,
                 f"_cancel_exchange_tps {symbol}: {e}", logging.WARNING)
            return False
        except Exception as e:
            flag("TPCANCEL-UNEXPECTED", origen_de_excepcion(e),
                 f"_cancel_exchange_tps {symbol}: {e}", logging.WARNING)
            return False

    async def _place_exchange_tps(self, symbol: str, side: str, qty: float,
                                  tp1_price: float, tp2_price: float) -> bool:
        """[TPO-3c] Coloca TP1+TP2 en Bitget. ALL-OR-NOTHING: si cualquiera
        falla se cancela todo y devuelve False -> el llamador marca
        exchange_tps=False y el TP lo ejecuta el bot (sin doble TP).

        [FIX-H7] GARANTIA DE CARGA DE LOS DOS TP:
          (a) Ningun estado MIXTO: si falla UN TP se cancela el otro (si el
              cancel a su vez falla -> flag TP-CANCEL-STUCK y el reconcile
              de 600s lo retira en modo bot).
          (b) Reclasifica el fallo ANTES de rendirse:
              DEFINITIVO (notional < minimo o step no divisible) -> sin
              reintento: cae a bot-side al instante (no se gastan APIs ni
              segundos; es el caso habitual con balances pequenos).
              TRANSITORIO (429/500/red/400 puntual) -> UN reintento tras
              cfg[tp_retry_sec]; si vuelve a fallar -> flag TP-RETRY-FAIL
              + bot-side.
          (c) Red de seguridad final (existe desde TPO-2): el bot ejecuta
              TP1/TP2 cada 15s via sl_tp.tp_accion y el SL SIEMPRE esta
              precargado en la orden de Bitget -> nunca quedas sin salida.
        """
        tp1_qty = tp2_qty = 0.0
        fallo_definitivo = False
        try:
            await self._cancel_exchange_tps(symbol)      # limpia previas
            market = await self._exch_call("market", symbol)
            step = (market["limits"]["amount"]["min"]
                    or 10 ** -market["precision"]["amount"])
            tp1_qty, tp2_qty = sl_tp.split_tp_qty(
                qty, self.cfg["tp1_close_frac"], step)
            if tp1_qty <= 0:
                # [H6] ORIGEN_CODIGO: tp1_qty sale de NUESTRO split_tp_qty
                # contra el step del mercado; la division es de esta logica.
                flag("TP-SPLIT", ORIGEN_CODIGO,
                     f"{symbol}: qty={qty} no divisible para TP1/TP2 "
                     f"(step={step}) -> fallback bot-side.", logging.WARNING)
                return False

            # --- [H7a] ¿el fallo seria DEFINITIVO o TRANSITORIO? ---
            min_notional = (market.get("limits", {}).get("cost", {})
                            .get("min") or 5.0)
            fallo_definitivo = any(
                not (math.isfinite(q * p) and q * p >= min_notional)
                for q, p in ((tp1_qty, tp1_price), (tp2_qty, tp2_price)))

            for intento in (1, 2):
                if intento == 2:
                    # [H7b] reintento SOLO ante fallo transitorio
                    await asyncio.sleep(
                        max(0.0, float(self.cfg.get("tp_retry_sec", 3))))
                    await self._cancel_exchange_tps(symbol)  # limpia tp1 huérfano
                ok1 = await self._place_tp_order(symbol, side, tp1_price,
                                                 tp1_qty, "tp1")
                ok2 = ok1 and await self._place_tp_order(
                    symbol, side, tp2_price, tp2_qty, "tp2")
                if ok1 and ok2:
                    log.info(f"{symbol} TP1+TP2 cargados en Bitget "
                             f"{'(tras reintento)' if intento == 2 else ''}: "
                             f"{tp1_qty}/{tp2_qty}")
                    return True
                if fallo_definitivo:
                    break                       # no hay nada que reintentar
                if intento == 1:
                    log.warning(f"{symbol}: carga de TP fallo (intento 1, "
                                f"posible 429/500/red). Reintento en "
                                f"{self.cfg.get('tp_retry_sec', 3)}s.")

            # --- llega aqui: fallo definitivo o 2 intentos agotados ---
            if not fallo_definitivo:
                # transitorio y agotado (el definitivo ya emitio
                # TP-NOTIONAL-MIN / TP-SPLIT dentro de _place_tp_order)
                flag("TP-RETRY-FAIL", ORIGEN_CODIGO,
                     f"{symbol}: TP1/TP2 no colocados tras 2 intentos "
                     f"(429/500/red); TP pasa a bot-side "
                     f"(sl_tp.tp_accion cada 15s).", logging.WARNING)
            if not await self._cancel_exchange_tps(symbol):
                flag("TP-CANCEL-STUCK", ORIGEN_CODIGO,
                     f"{symbol}: limpieza de TP residual fallo; el reconcile "
                     f"de {self.cfg.get('tp_reconcile_sec', 600)}s lo retirara "
                     f"en modo bot.", logging.ERROR)
            return False
        except Exception as e:
            flag("TPSET-UNEXPECTED", origen_de_excepcion(e),
                 f"_place_exchange_tps {symbol}: {e}", logging.ERROR)
            await self._cancel_exchange_tps(symbol)
            return False

    async def _place_single_tp(self, symbol: str, side: str, qty: float,
                               trigger_price: float) -> bool:
        """[H8] Fallback de 2do nivel: si NO se pudieron cargar TP1+TP2
        (all-or-nothing), coloca UNA sola profit_plan que cierra el 100%
        de la posicion en tp1_pct (ej: +2% de precio = +20% PnL a 10x).
        Si esa tampoco cabe (notional < minimo de Bitget) -> False y el
        TP lo ejecuta el bot (red final, sl_tp.tp_accion)."""
        if not await self._place_tp_order(symbol, side, trigger_price, qty,
                                          "tp1"):
            # el flag (TP-NOTIONAL-MIN / 429 / etc.) ya lo emitio
            # _place_tp_order; aqui solo reportamos la desviacion.
            log.warning(f"{symbol}: TP unico 100% tampoco pudo cargarse "
                        f"(qty={qty} @ {trigger_price}).")
            return False
        log.info(f"{symbol} TP UNICO cargado en Bitget: 100% de la posicion "
                 f"en {trigger_price} (+{self.cfg['tp1_pct']*100:.0f}% precio "
                 f"= +{self.cfg['tp1_pct']*self.cfg['leverage']*100:.0f}% PnL "
                 f"a {self.cfg['leverage']:.0f}x).")
        return True

    @staticmethod
    def _es_tp_single(te: dict, plans: Optional[list],
                      contracts: float) -> bool:
        """[H8] ¿El estado/dato es modo TP UNICO (1 orden = 100%)?
        True/False declarado en te, o (te reconstruido / JSON viejo sin la
        clave: tp_single ausente o None) se DEDUCE del tamano del plan
        pendiente: size == contracts (100%) -> es el TP unico."""
        v = te.get("tp_single", None)
        if v is True:
            return True
        if v is False:
            return False
        for o in plans or []:
            try:
                sz = float(o.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if contracts > 0 and \
                    abs(sz - contracts) <= max(abs(contracts) * 1e-3, 1e-12):
                return True
        return False

    async def _reportar_tps_visibles(self, symbol: str) -> None:
        """[H8] Confirmacion CONTRA Bitget de los planes realmente
        pendientes (lo que veras en el panel) -> log + Telegram.
        Nunca lanza excepcion."""
        try:
            planes = await self._pending_profit_plans(symbol)
            if planes is None:
                return                       # error ya flageado
            if not planes:
                flag("TP-VISIBLE", ORIGEN_CODIGO,
                     f"{symbol}: 0 profit_plan pendientes en Bitget "
                     f"(usar verificar_tp.py para inspeccionar).",
                     logging.WARNING)
                return
            resumen = ", ".join(
                f"{o.get('triggerPrice')} x{o.get('size')}" for o in planes)
            log.info(f"{symbol} planes PENDIENTES visibles en Bitget "
                     f"({len(planes)}): {resumen}")
            await self.send_telegram(
                f"*{symbol}* TP visible en Bitget ({len(planes)}): "
                f"`{resumen}`")
        except Exception as e:
            flag("TP-VISIBLE", origen_de_excepcion(e),
                 f"_reportar_tps_visibles {symbol}: {e}", logging.WARNING)

    async def _pending_profit_plans(self, symbol: str) -> Optional[list]:
        """[TPO-3d] profit_plan PENDIENTES del simbolo.
        None = error (ya flageado); lista = puede estar vacia."""
        try:
            params = {"planType": "profit_loss",
                      "productType": "USDT-FUTURES",
                      "symbol": self._bsymbol(symbol), "limit": "50"}
            body = await self._exch_call(
                "private_mix_get_v2_mix_order_orders_plan_pending", params)
            if isinstance(body, dict):
                code = str(body.get("code", "00000"))
                if code not in ("00000", "0"):
                    flag("TPPEND", ORIGEN_BIBLIOTECA,
                         f"{symbol}: code={code} "
                         f"{str(body.get('msg'))[:120]}", logging.WARNING)
                    return None
            return [o for o in self._plan_list(body)
                    if o.get("planType") == "profit_plan"]
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_pending_profit_plans {symbol}: rate limit.",
                 logging.WARNING)
            await asyncio.sleep(5)
            return None
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA,
                 f"_pending_profit_plans {symbol}: {e}", logging.WARNING)
            return None
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_pending_profit_plans {symbol}: error de red.",
                 logging.WARNING)
            return None
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA,
                 f"_pending_profit_plans {symbol}: {e}", logging.WARNING)
            return None
        except Exception as e:
            flag("TPPEND-UNEXPECTED", origen_de_excepcion(e),
                 f"_pending_profit_plans {symbol}: {e}", logging.WARNING)
            return None

    async def _tp1_executed_en_bitget(self, symbol: str,
                                      tp1_fmt: float) -> bool:
        """[TPO-3e] ¿Existio una orden profit_plan con ESTE trigger de TP1
        y fue EJECUTADA? (historia de planes). Cualquier error -> False
        (no afirma)."""
        try:
            params = {"planType": "profit_loss",
                      "productType": "USDT-FUTURES",
                      "symbol": self._bsymbol(symbol),
                      "planStatus": "executed", "limit": "50"}
            body = await self._exch_call(
                "private_mix_get_v2_mix_order_orders_plan_history", params)
            if isinstance(body, dict):
                code = str(body.get("code", "00000"))
                if code not in ("00000", "0"):
                    flag("TPHIST", ORIGEN_BIBLIOTECA,
                         f"{symbol}: code={code}", logging.WARNING)
                    return False
            for o in self._plan_list(body):
                if o.get("planType") != "profit_plan":
                    continue
                try:
                    trig = float(o.get("triggerPrice", 0))
                except (TypeError, ValueError):
                    continue
                if abs(trig - tp1_fmt) <= max(abs(tp1_fmt) * 1e-4, 1e-9):
                    return True
            return False
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_tp1_executed_en_bitget {symbol}: rate limit.",
                 logging.WARNING)
            await asyncio.sleep(5)
            return False
        except Exception as e:
            flag("TPHIST-UNEXPECTED", origen_de_excepcion(e),
                 f"_tp1_executed_en_bitget {symbol}: {e}", logging.WARNING)
            return False

    async def _reconcile_exchange_tps(self, symbol: str, side: str,
                                      te: dict, contracts: float,
                                      profit_pct: float) -> None:
        """[TPO-3f] Reconciliacion (throttle tp_reconcile_sec) de que los
        TP1/TP2 existan en Bitget:
          * TP1 ausente ya EJECUTADO -> confirma tp1_done (gate BE).
          * TP1/TP2 ausentes sin ejecutar -> re-colocar; si no es posible
            -> cancelar todo y exchange_tps=False (fallback bot-side).
        """
        now = time.time()
        if now - self._tp_recon_last.get(symbol, 0.0) < \
                float(self.cfg.get("tp_reconcile_sec", 600)):
            return
        self._tp_recon_last[symbol] = now
        if not te.get("exchange_tps", False):
            # modo bot: NO debe haber profit_plan colgando (limpiar restos)
            restos = await self._pending_profit_plans(symbol)
            if restos:
                await self._cancel_exchange_tps(symbol)
            return
        plans = await self._pending_profit_plans(symbol)
        if plans is None:
            return                              # error ya flageado
        try:
            f1 = float(await self._exch_call("price_to_precision", symbol,
                                             float(te["tp1_price"])))
            f2 = float(await self._exch_call("price_to_precision", symbol,
                                             float(te["tp2_price"])))
        except Exception as e:
            flag("TPREC-PRICE", origen_de_excepcion(e),
                 f"{symbol}: precision de trigger TP: {e}", logging.WARNING)
            return

        def _match(o, f):
            try:
                return abs(float(o.get("triggerPrice", 0)) - f) \
                    <= max(abs(f) * 1e-4, 1e-9)
            except (TypeError, ValueError):
                return False

        # --- [H8] Modo TP UNICO: 1 sola orden = 100% en tp1_pct ---
        if self._es_tp_single(te, plans, contracts):
            def _full(o):
                try:
                    return abs(float(o.get("size") or 0) - contracts) \
                        <= max(abs(contracts) * 1e-3, 1e-12)
                except (TypeError, ValueError):
                    return False
            if any(_match(o, f1) and _full(o) for o in plans):
                return                          # el TP unico sigue ahi
            if not te.get("tp1_done", False) and \
                    await self._tp1_executed_en_bitget(symbol, f1):
                # el TP unico (100%) ya ejecuto -> cierre completo
                te["tp1_done"] = True
                te["tp2_done"] = True
                await self._save_trade_entries()
                log.info(f"{symbol} TP unico ejecutado por Bitget (100%).")
                await self.send_telegram(
                    f"*{symbol}* TP unico ejecutado (100%)")
                return
            # reparar: limpia restos y re-coloca 1 TP = 100% de contracts
            await self._cancel_exchange_tps(symbol)
            if await self._place_single_tp(symbol, side, contracts,
                                           float(te["tp1_price"])):
                te["tp_single"] = True
                await self._save_trade_entries()
                log.info(f"{symbol} TP unico reparado en Bitget (100%).")
                await self.send_telegram(
                    f"*{symbol}* TP unico en Bitget reparado (100%)")
                return
            te["exchange_tps"] = False
            te["tp_single"] = False
            await self._save_trade_entries()
            flag("TP-FALLBACK", ORIGEN_CODIGO,
                 f"{symbol}: TP unico no pudo re-colocarse -> bot-side.",
                 logging.WARNING)
            return

        p1 = any(_match(o, f1) for o in plans)
        p2 = any(_match(o, f2) for o in plans)

        # --- ¿TP1 ausente porque ya se ejecuto (p.ej. tras restart)? ---
        if not p1 and not te.get("tp1_done", False):
            if await self._tp1_executed_en_bitget(symbol, f1):
                te["tp1_done"] = True
                await self._save_trade_entries()
                log.info(f"{symbol} TP1 ejecutado por Bitget "
                         f"(confirmado en historia).")
                await self.send_telegram(
                    f"*{symbol}* TP1 ejecutado (Bitget)")
                p1 = True

        falta = []
        if not p1 and not te.get("tp1_done", False):
            falta.append(("tp1", float(te["tp1_price"])))
        if not p2:
            falta.append(("tp2", float(te["tp2_price"])))
        if not falta:
            return

        # --- Re-colocar los TP faltantes (all-or-nothing) ---
        try:
            market = await self._exch_call("market", symbol)
            step = (market["limits"]["amount"]["min"]
                    or 10 ** -market["precision"]["amount"])
            if te.get("tp1_done", False):
                # TP1 ya ejecutado: TP2 cierra TODO lo que queda
                qmap = {"tp1": 0.0, "tp2": round(contracts, 12)}
            else:
                t1q, t2q = sl_tp.split_tp_qty(
                    contracts, self.cfg["tp1_close_frac"], step)
                qmap = {"tp1": t1q, "tp2": t2q}
        except Exception as e:
            flag("TPREC-SPLIT", origen_de_excepcion(e),
                 f"{symbol}: {e}", logging.WARNING)
            return
        for tag, px in falta:
            if qmap.get(tag, 0) <= 0 or not await self._place_tp_order(
                    symbol, side, px, qmap[tag], tag):
                await self._cancel_exchange_tps(symbol)
                # [H8] 2 TP imposibles -> intentar 1 TP = 100% @ tp1
                # (mismo fallback de open_position; caso comun tras
                # restart cuando el TP2 no cabe en notional minimo).
                # No aplica si tp1 ya ejecuto (el trigger de tp1 ya fue
                # cruzado -> cerraria al instante en vez de esperar tp2).
                if self.cfg.get("tp_single_fallback", True) and \
                        not te.get("tp1_done", False) and \
                        await self._place_single_tp(
                            symbol, side, contracts,
                            float(te["tp1_price"])):
                    te["exchange_tps"] = True
                    te["tp_single"] = True
                    await self._save_trade_entries()
                    flag("TP-SINGLE-FALLBACK", ORIGEN_CODIGO,
                         f"{symbol}: {tag} no re-colocable -> TP unico "
                         f"100% en Bitget (+{self.cfg['tp1_pct']*100:.0f}%).",
                         logging.WARNING)
                    await self.send_telegram(
                        f"*{symbol}* TP unico 100% cargado en Bitget")
                    return
                te["exchange_tps"] = False
                te["tp_single"] = False
                await self._save_trade_entries()
                flag("TP-FALLBACK", ORIGEN_CODIGO,
                     f"{symbol}: re-colocacion de {tag} en Bitget fallo -> "
                     f"TP pasa a bot-side.", logging.WARNING)
                return
        nombres = ", ".join(t for t, _ in falta)
        log.info(f"{symbol} TP reparado en Bitget: {nombres}")
        await self.send_telegram(
            f"*{symbol}* TP en Bitget reparado ({nombres})")

    def _reconstruir_te(self, symbol: str, pos: dict, balance: float) -> dict:
        """[TPO-3g] Reconstruye trade_entries perdido (Render: disco
        efimero). Deriva TODO de datos del exchange + CONFIG (nunca
        inventa valores ajenos); entry_time = ahora (limitacion: fills
        anteriores al restart pueden quedar fuera del CSV)."""
        entry = float(pos["entryPrice"])
        side = pos.get("side", "long")
        signo = 1.0 if side == "long" else -1.0
        f1, f2 = self.cfg["tp1_pct"], self.cfg["tp2_pct"]
        minsl = self.cfg["min_sl_dist_pct"]
        te = {
            "entry_time": datetime.now().isoformat(),
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            # aproximacion al preset SL original (solo para CSV/BE)
            "sl_price": entry * (1 - signo * minsl),
            "tp1_price": entry * (1 + signo * f1),
            "tp2_price": entry * (1 + signo * f2),
            "tp1_done": False,
            "tp2_done": False,
            "quantity": float(pos.get("contracts", 0)),
            "balance_before": float(balance or 0.0),
            "size_usdt": 0.0,
            "risk_pct": 0.0,
            "exchange_tps": True,   # optimista: lo refina el reconcile
            # [H8] None = desconocido tras rebuild; el reconcile lo DEDUCE
            # del tamano del plan pendiente (size == contracts -> unico).
            "tp_single": None,
        }
        flag("TE-REBUILD", ORIGEN_CODIGO,
             f"{symbol}: trade_entries reconstruido tras restart "
             f"(entry_time=ahora; reconcile refina TP).", logging.WARNING)
        return te

    async def open_position(self, symbol: str, side: str, sl_price: float,
                            tp_price: float, tp2_price: float = None,
                            balance: float = None,
                            df: pd.DataFrame = None, entry_idx: int = None,
                            vwap_value: float = None) -> bool:
        """
        [8.6b] Abre posicion MARKET en Bitget con SL precargado.
          1) Valida precio/SL/TP1/TP2 finitos y coherentes.
          2) Recalcula SL/TP1/TP2 con el precio REAL de entrada.
          3) Sizing: margen = balance * risk_pct, notional = margen * leverage.
          4) Respeta precision/step y notional minimo de Bitget (~5 USDT).
          5) Orden market con presetStopLossPrice. [TPO-3] TP1/TP2 se
             colocan como ordenes profit_plan EN BITGET (visibles y las
             ejecuta el exchange); si el exchange no los acepta, fallback
             a ejecucion bot-side (manage_positions, TPO-2).
          6) Alerta Telegram + persistencia en trade_entries.json.
        """
        if symbol in self.session_active:
            log.debug(f"{symbol} ya tiene posicion activa. Saltando.")
            return False

        # [FIX-3] Ultima linea de defensa: si el simbolo acaba de cerrar,
        # no se re-entra aunque otro llamador lo intente.
        if self.en_cooldown_simbolo(symbol):
            flag("RISK-SYM-COOLDOWN", ORIGEN_CODIGO,
                 f"{symbol}: en cooldown de 1h tras su cierre. Orden omitida.",
                 logging.WARNING)
            return False

        if balance is None:
            balance = await self.get_balance()
        if balance <= 0:
            flag("BAL-INSUF", ORIGEN_DATOS,
                 f"Balance insuficiente para {symbol}.", logging.WARNING)
            return False

        try:
            ticker = await self._exch_call("fetch_ticker", symbol)
            price = float(ticker["last"])

            # Precio de referencia de la senal: CLOSE de la vela de senal
            # (precio de confirmacion; la vela ya esta CERRADA - ver FIX-2).
            # Antes usaba open(), lo que mezclaba distancias SL/TP ancladas
            # en close (las calcula _sl_tp_desde_canal con close_last) con un
            # denominador distinto -> distancias sesgadas.
            strategy_entry = (float(df.iloc[entry_idx]["close"])
                              if df is not None and entry_idx is not None
                              and entry_idx < len(df) else price)

            # --- Validacion + reescalado de SL/TP1/TP2: logica en sl_tp.py ---
            #     (NaN/Inf, SL en (0,10%], 0 < tp1 < tp2; distancias medidas
            #      sobre la senal close y re-escaladas al precio REAL de
            #      entrada. None -> flag + no abrir.)
            if tp2_price is None:      # robustez: se deriva del precio real
                f1 = self.cfg["tp1_pct"]
                f2 = self.cfg["tp2_pct"]
                if side == "long":
                    tp2_price = price * (1 + f2)
                else:
                    tp2_price = price * (1 - f2)
            v = sl_tp.validar_entrada(symbol, side, strategy_entry, price,
                                      sl_price, tp_price, tp2_price)
            if v is None:
                return False
            sl_price, tp1_price, tp2_price = v["sl"], v["tp1"], v["tp2"]
            sl_dist = v["sl_dist"]

            # --- SIZING de la posicion ---
            risk_pct = self.cfg["risk_pct"]
            leverage = self.cfg["leverage"]
            target_margin = balance * risk_pct      # margen objetivo en USDT
            pos_value = target_margin * leverage    # valor nominal (notional)
            raw_qty = pos_value / price             # cantidad bruta

            market = await self._exch_call("market", symbol)
            precision = market["precision"]["amount"]
            step = market["limits"]["amount"]["min"] or (10 ** -precision)

            qty = (raw_qty // step) * step          # redondeo al step del mercado
            if qty <= 0:
                qty = step                          # minimo 1 step

            actual_margin = (qty * price) / leverage
            if actual_margin > target_margin:       # no exceder el riesgo objetivo
                qty -= step
                if qty <= 0:
                    qty = step
                actual_margin = (qty * price) / leverage

            # [FIX-H1] El step MINIMO del mercado puede FORZAR un margen mayor
            # que el objetivo: una vez qty=step ya no puede bajar mas y el
            # clamp de arriba es un no-op -> risk_pct quedaria IGNORADO.
            # Ejemplo real (XAUT step=0.01, balance 18.4): objetivo 1.29 USDT,
            # real 4.11 USDT (3.2x -> perdida en el 5% de SL = 11.2% del
            # balance en vez de 3.5%). En ese caso el par es INOPERABLE con
            # este balance: se RECHAZA la orden en vez de arriesgar de mas.
            if target_margin > 0 and actual_margin > target_margin * 1.05:
                flag("RISK-MINSTEP", ORIGEN_CODIGO,
                     f"{symbol}: step={step} obliga a margen "
                     f"{actual_margin:.2f} USDT > objetivo "
                     f"{target_margin:.2f} USDT "
                     f"(x{actual_margin / target_margin:.1f}). Par "
                     f"inoperable con este balance; orden omitida.",
                     logging.WARNING)
                return False

            log.info(f"[SIZING] {symbol} | Objetivo: {target_margin:.2f} | "
                     f"Real: {actual_margin:.2f} | Qty: {qty}")

            # --- Notional minimo de Bitget (~5 USDT) ---
            min_notional = market.get("limits", {}).get("cost", {}).get("min") or 5.0
            notional = qty * price
            if notional < min_notional:
                flag("NOTIONAL-MIN", ORIGEN_BIBLIOTECA,
                     f"{symbol}: notional {notional:.2f} < minimo {min_notional} USDT.",
                     logging.WARNING)
                return False

            # --- Envio de la orden (thread: no bloquea el loop) ---
            # [TPO-2] solo presetStopLossPrice: el TP no va al exchange.
            ccxt_side = "buy" if side == "long" else "sell"
            fmt_tp1 = await self._exch_call("price_to_precision", symbol,
                                            tp1_price)
            fmt_tp2 = await self._exch_call("price_to_precision", symbol,
                                            tp2_price)
            fmt_sl = await self._exch_call("price_to_precision", symbol, sl_price)
            params = {
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "tradeSide": "open",
                "presetStopLossPrice": str(fmt_sl),      # Stop Loss (exchange)
            }
            await self._exch_call("create_order", symbol, "market", ccxt_side,
                                  qty, None, params)
            fmt_price = await self._exch_call("price_to_precision", symbol, price)

            # --- [TPO-3] TP1/TP2 como ordenes profit_plan EN BITGET ---
            #     (visibles en el panel de la posicion; las ejecuta el
            #      exchange). All-or-nothing: si falla -> fallback bot.)
            exchange_tps = False
            tp_single = False
            if self.cfg.get("tp_mode") == "exchange":
                exchange_tps = await self._place_exchange_tps(
                    symbol, side, qty, tp1_price, tp2_price)
                if not exchange_tps and \
                        self.cfg.get("tp_single_fallback", True):
                    # [H8] 2 TPs no cargables -> 1 sola TP en Bitget que
                    # cierra el 100% de la posicion en tp1_pct (+2% =
                    # +20% PnL a 10x). exchange_tps queda True => el bot
                    # NO ejecuta tp_accion (sin doble TP).
                    tp_single = await self._place_single_tp(
                        symbol, side, qty, tp1_price)
                    exchange_tps = tp_single
                    if tp_single:
                        flag("TP-SINGLE-FALLBACK", ORIGEN_CODIGO,
                             f"{symbol}: TP1+TP2 no cargables -> TP unico "
                             f"100% en +{self.cfg['tp1_pct']*100:.0f}% "
                             f"(+{self.cfg['tp1_pct']*self.cfg['leverage']*100:.0f}% "
                             f"PnL a {self.cfg['leverage']:.0f}x).",
                             logging.WARNING)
                if not exchange_tps:
                    flag("TP-BOTFALLBACK", ORIGEN_CODIGO,
                         f"{symbol}: TP1/TP2 no se colocaron en Bitget -> "
                         f"ejecucion TP bot-side.", logging.WARNING)
            if tp_single:
                tps_txt = "Bitget (TP unico 100%)"
            elif exchange_tps:
                tps_txt = "Bitget (visible)"
            else:
                tps_txt = "bot (fallback)"

            # --- Alerta de texto a Telegram (TP1 cierra 50%, TP2 el resto) ---
            if tp_single:
                tp_l1 = (f"TP1: `{fmt_tp1}` (+{self.cfg['tp1_pct']*100:.0f}% -> "
                         f"cierra 100% [unico TP])\n")
                tp_l2 = ""
            else:
                tp_l1 = (f"TP1: `{fmt_tp1}` (+{self.cfg['tp1_pct']*100:.0f}% -> "
                         f"cierra {self.cfg['tp1_close_frac']*100:.0f}%)\n")
                tp_l2 = (f"TP2: `{fmt_tp2}` (+{self.cfg['tp2_pct']*100:.0f}% -> "
                         f"cierre total)\n")
            msg = (
                f"*{symbol} {side.upper()}*\n"
                f"Entrada: `{fmt_price}`\n"
                f"SL: `{fmt_sl}` (-{self.cfg['min_sl_dist_pct']*100:.0f}% "
                f"precio / -{self.cfg['min_sl_dist_pct']*self.cfg['leverage']*100:.0f}% "
                f"margen {self.cfg['leverage']:.0f}x)\n"
                f"{tp_l1}{tp_l2}"
                f"TPs: {tps_txt}\n"
                f"Qty: `{qty}` | Margen: `{actual_margin:.2f}` USDT"
            )
            if vwap_value is not None and math.isfinite(vwap_value):
                msg += f"\nVWAP: `{vwap_value:.6f}`"
            await self.send_telegram(msg)
            if exchange_tps:
                # [H8] confirmacion post-carga: que quedo REALMENTE
                # visible en Bitget (log + Telegram con trigger/size).
                await self._reportar_tps_visibles(symbol)
            log.info(f"{symbol} {side.upper()} | Entry={fmt_price} SL={fmt_sl} "
                     f"TP1={fmt_tp1} TP2={fmt_tp2} | "
                     f"TPs={'single' if tp_single else ('exchange' if exchange_tps else 'bot')} | "
                     f"Qty={qty} | Margin={actual_margin:.2f}")

            # --- Memoria de la entrada + persistencia ---
            self.trade_entries[symbol] = {
                "entry_time": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "entry_price": price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp1_done": False,     # [TPO-2] TP1 aun no ejecutado
                "tp2_done": False,     # [TPO-2] TP2 aun no ejecutado
                "exchange_tps": exchange_tps,   # [TPO-3] TP en Bitget?
                "tp_single": tp_single,   # [H8] 1 sola TP = 100% en +2%?
                "quantity": qty,
                "balance_before": balance,
                "size_usdt": round(actual_margin, 2),
                "risk_pct": round(actual_margin / balance * 100, 2),
            }
            await self._save_trade_entries()
            self.session_active.add(symbol)
            return True

        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA, f"open_position {symbol}: {e}", logging.ERROR)
            return False
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"open_position {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except AuthenticationError as e:
            flag("AUTH", ORIGEN_BIBLIOTECA, f"open_position {symbol}: {e}", logging.CRITICAL)
            return False
        except PermissionDenied as e:
            flag("PERM", ORIGEN_BIBLIOTECA, f"open_position {symbol}: {e}", logging.CRITICAL)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"open_position {symbol}: error de red.", logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"open_position {symbol}: {e}", logging.ERROR)
            return False
        except Exception as e:
            flag("OPEN-UNEXPECTED", origen_de_excepcion(e),
                 f"Abriendo {symbol}: {e}", logging.ERROR)
            return False

    async def manage_positions(self, balance: float = None):
        """
        [8.6c] Gestion ciclica de posiciones abiertas:
          1) Detecta posiciones cerradas por el exchange (TP/SL/manual).
          2) Tracking de precios pico (trailing) y adverso (reportes).
          2.5) TP1/TP2 [TPO-3]: en modo exchange los ejecuta Bitget
             (profit_plan) y aqui solo se reconcilian/detecta el fill
             del TP1; fallback bot-side: +2% -> 50%, +3% -> TODO.
          3) BREAK EVEN: SOLO tras TP1 (be_after_tp1) y si profit >= trigger.
          4) TRAILING STOP (apagado con trailing_enabled=False; logica
             intacta en sl_tp.py [3]).
        """
        if balance is None:
            balance = await self.get_balance()

        try:
            positions = await self._exch_call("fetch_positions")
            active_symbols = [p["symbol"] for p in positions
                              if float(p["contracts"]) > 0]

            # 1) Detectar posiciones que desaparecieron (ya cerradas)
            for sym in list(self.session_active):
                if sym not in active_symbols:
                    self.cooldowns[sym] = time.time() + 3600  # pausa 1h
                    log.info(f"{sym} CERRADA. Cooldown 1h activado.")
                    await self._process_closed_position(sym)
                    self._cleanup_symbol(sym)

            # 2) Gestionar cada posicion viva
            for pos in positions:
                symbol = pos["symbol"]
                side = pos["side"]
                if float(pos["contracts"]) == 0:
                    continue

                entry = float(pos["entryPrice"])
                mark = float(pos["markPrice"])
                if entry <= 0:
                    flag("POS-ENTRY0", ORIGEN_DATOS,
                         f"{symbol}: entryPrice=0, ignorando posicion.", logging.WARNING)
                    continue
                profit_pct = ((mark - entry) / entry if side == "long"
                              else (entry - mark) / entry)

                # =====================================================
                # 2.5) TP1 / TP2 — DOS TP porcentuales sobre la entrada
                #   [TPO-3] tp_mode="exchange" (default): los TP son
                #   ordenes profit_plan EN BITGET (visibles y las ejecuta
                #   el exchange). El bot solo:
                #     (a) detecta el fill del TP1 (posicion encogida 25%+)
                #         -> tp1_done para el gate del BE,
                #     (b) reconcilia (tp_reconcile_sec) que las ordenes
                #         sigan ahi; si faltan las re-coloca; si no puede
                #         -> exchange_tps=False y el TP lo ejecuta el BOT
                #         (fallback TPO-2 con sl_tp.tp_accion; NUNCA ambos).
                #   te["exchange_tps"]=False (p.ej. notional < minimo):
                #   ejecucion bot-side completa (TPO-2): TP1 -> cierre
                #   parcial 50%; TP2 -> cierre TOTAL (o gap a TP2).
                #   Estados tp1_done/tp2_done en trade_entries (persistido).
                # =====================================================
                tp1_done = False
                te = self.trade_entries.get(symbol)
                if te is None:
                    # [TPO-3] disco efimero de Render: reconstruir desde
                    # el exchange + CONFIG (flag TE-REBUILD).
                    te = self._reconstruir_te(symbol, pos, balance)
                    self.trade_entries[symbol] = te
                    await self._save_trade_entries()
                if te.get("tp2_done", False):
                    # TP2 ya ejecutado (cierre en vuelo): NO seguir con
                    # tracking/BE/trailing de una posicion que se esta
                    # cerrando (evita ordenes de SL inutiles).
                    continue

                # (a) TP1 del exchange ya ejecuto? (encogido 25%+)
                if not te.get("tp1_done", False):
                    qty_orig = float(te.get("quantity") or 0)
                    if qty_orig > 0 and float(pos["contracts"]) < qty_orig * 0.75:
                        te["tp1_done"] = True
                        await self._save_trade_entries()
                        log.info(f"{symbol} TP1 ejecutado por Bitget "
                                 f"(50% cerrado).")
                        await self.send_telegram(
                            f"*{symbol}* TP1 ejecutado (Bitget)")
                tp1_done = bool(te.get("tp1_done", False))

                if self.cfg.get("tp_mode") == "exchange" and \
                        te.get("exchange_tps", False):
                    # (b) el exchange ejecuta: solo reconciliar ordenes
                    await self._reconcile_exchange_tps(
                        symbol, side, te, float(pos["contracts"]),
                        profit_pct)
                else:
                    # --- FALLBACK bot-side [TPO-2] (sin TP en exchange) ---
                    accion = sl_tp.tp_accion(profit_pct, tp1_done, self.cfg)
                    if accion == "tp2":
                        # Cierre TOTAL de la posicion (close_position ->
                        # flash close del exchange, ya probado en prod).
                        if await self.close_position(symbol):
                            te["tp2_done"] = True
                            await self._save_trade_entries()
                            log.info(f"{symbol} TP2 (+{self.cfg['tp2_pct']*100:.0f}%)"
                                     f" CIERRE TOTAL. profit="
                                     f"{profit_pct*100:+.2f}%")
                            await self.send_telegram(
                                f"*{symbol}* TP2 (cierre total) — profit "
                                f"{profit_pct*100:+.2f}%")
                    elif accion == "tp1":
                        # [FIX-H2] Cierre parcial TP1: _close_partial decide
                        # el modo real (no siempre es el 50% configurado,
                        # porque el step del mercado cuantiza la fraccion):
                        #   "parcial" -> cerro close_qty (< qty)
                        #   "total"   -> qty indivisible -> cerro TODO
                        #                (flag TP1-NOSPLIT) y NO queda nada
                        #                que gestionar: se marca tp2_done y
                        #                se salta BE/trailing de este ciclo.
                        modo = await self._close_partial(
                            symbol, side, float(pos["contracts"]),
                            self.cfg["tp1_close_frac"])
                        if modo:
                            te["tp1_done"] = True
                            tp1_done = True
                            if modo == "total":
                                te["tp2_done"] = True
                                await self._save_trade_entries()
                                log.info(f"{symbol} TP1 "
                                         f"(+{self.cfg['tp1_pct']*100:.0f}%) "
                                         f"CIERRE TOTAL (qty no divisible al "
                                         f"step). profit={profit_pct*100:+.2f}%")
                                await self.send_telegram(
                                    f"*{symbol}* TP1 = cierre TOTAL "
                                    f"(qty no divisible al step) — profit "
                                    f"{profit_pct*100:+.2f}%")
                                continue
                            await self._save_trade_entries()
                            log.info(f"{symbol} TP1 (+{self.cfg['tp1_pct']*100:.0f}%)"
                                     f" PARCIAL {self.cfg['tp1_close_frac']*100:.0f}%"
                                     f" (step) cerrado. profit={profit_pct*100:+.2f}%")
                            await self.send_telegram(
                                f"*{symbol}* TP1 (cierra "
                                f"{self.cfg['tp1_close_frac']*100:.0f}%) — "
                                f"profit {profit_pct*100:+.2f}%")

                # Tracking del peor precio (max adverse excursion)
                if symbol not in self.adverse_prices:
                    self.adverse_prices[symbol] = mark
                elif side == "long":
                    self.adverse_prices[symbol] = min(self.adverse_prices[symbol], mark)
                else:
                    self.adverse_prices[symbol] = max(self.adverse_prices[symbol], mark)

                # Tracking del mejor precio (max favorable / picos trailing)
                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mark
                elif side == "long":
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mark)
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mark)

                # =====================================================
                # 3) BREAK EVEN (BE) - se evalua UNA sola vez por posicion
                # =====================================================
                # QUE HACE EL BREAK EVEN (sin conocimientos previos):
                #   Cuando el precio ya va a favor por be_trigger_pct (1.32%),
                #   movemos el Stop Loss hasta la ENTRADA +/- be_offset_pct
                #   (0.2%). Asi ese trade YA NO PUEDE terminar en perdida:
                #   como minimo sale con un +/-0.2% (break even + comisiones).
                #
                # [FIX-5] CORRECCION DE RIESGO (antes era un bug):
                #   El codigo anterior escribia el SL del BE SIN MIRAR el SL
                #   que ya tenia la orden. Ejemplo real con entrada=100:
                #       mark=101.00 -> el TRAILING pone SL = 100.70
                #       mark=101.32 -> el BE dispara        -> SL = 100.20
                #   El BE tira el stop PARA ATRAS y se pierde 0.50% de
                #   proteccion ya ganada (peor aun si el trailing llevo el
                #   SL a entrada+1.64%: lo tiraba a entrada+0.20%).
                #
                #   REGLA NUEVA: solo se envia la orden si el SL del BE
                #   MEJORA el SL actual:
                #       LONG  -> el nuevo SL debe quedar MAS ALTO (mas cerca
                #                del precio) que el actual.
                #       SHORT -> el nuevo SL debe quedar MAS BAJO.
                #   Si no mejora, NO se llama a la API (se ahorra un rate
                #   limit y no se empeora la proteccion) y se marca el BE
                #   como ya gestionado para no re-evaluarlo cada 15s.
                #
                #   Estados posibles al salir de este bloque:
                #     - Orden aplicada      -> '_be'=True y '_be_price'=SL nuevo
                #     - No mejoraba (noop)  -> '_be'=True y '_be_price'=SL actual
                #     - La API fallo        -> '_be' SIN marcar (se reintenta
                #                              en el proximo ciclo de 15s)
                #   (logica PURA del BE -> sl_tp.py [2] BREAK EVEN;
                #    aqui SOLO se ejecuta la decision: API/estado/logs)
                #
                #   [BE-TP1] be_after_tp1=True: el BE SOLO se evalua si
                #   tp1_done=True (TP1 ya ejecutado). Antes del TP1 no hay
                #   BE (decision del usuario: el BE entra tras el TP1).
                # =====================================================
                if sl_tp.be_debe_evaluar(profit_pct, self.cfg,
                                         tp1_done=tp1_done):
                    if not self.alerts_history.get(f"{symbol}_be", False):

                        # (a) ¿CUAL ES EL STOP LOSS QUE TENEMOS AHORA?
                        #     1º el SL del TRAILING si esta activo (es el mas
                        #        reciente y el mas ajustado al precio),
                        #     2º si no, el SL ORIGINAL de la entrada.
                        sl_trailing = self.alerts_history.get(f"{symbol}_trail_sl")
                        sl_original = (self.trade_entries.get(symbol) or {}).get("sl_price")
                        sl_actual = float(sl_trailing if sl_trailing else
                                          (sl_original if sl_original else 0.0))

                        # (b) ¿CUAL SERIA EL SL DEL BREAK EVEN?
                        new_sl = sl_tp.be_objetivo(side, entry, self.cfg)

                        # (c) ¿EL BE MEJORA EL STOP ACTUAL?
                        #     LONG : subir el stop  -> new_sl > sl_actual
                        #     SHORT: bajar el stop  -> new_sl < sl_actual
                        #     (decision en sl_tp.be_mejora)
                        if not sl_tp.be_mejora(side, new_sl, sl_actual):
                            # El stop actual ya protege MAS que el BE propuesto:
                            # no se envia ninguna orden y no se empeora el riesgo.
                            log.info(
                                f"{symbol} BE omitido (no mejora): SL actual "
                                f"{sl_actual:.6f} ya protege mas que el BE "
                                f"{new_sl:.6f}.")
                            self.alerts_history[f"{symbol}_be"] = True
                            self.alerts_history[f"{symbol}_be_price"] = sl_actual

                        elif await self._update_stop_loss(symbol, side, new_sl):
                            # (d) Bitget acepto la orden: registrar el estado
                            self.alerts_history[f"{symbol}_be"] = True
                            self.alerts_history[f"{symbol}_be_price"] = new_sl
                            log.info(f"{symbol} BE activado "
                                     f"(offset {self.cfg['be_offset_pct']*100:.1f}%) "
                                     f"SL {sl_actual:.6f} -> {new_sl:.6f}")
                            await self.send_telegram(
                                f"*{symbol}* BE (offset "
                                f"{self.cfg['be_offset_pct']*100:.1f}%)")
                        # (e) Si la API fallo (_update_stop_loss -> False):
                        #     '_be' queda SIN marcar y se reintenta al tick
                        #     siguiente (el try/except del bucle ya esta ahi).

                # 4) TRAILING STOP  [TRAIL-OFF] solo si trailing_enabled=True
                #    (default False: logica CONSERVADA para activarla luego)
                trail_enabled = bool(self.cfg.get("trailing_enabled", False))
                trail_key = f"{symbol}_trail_active"
                trail_sl_key = f"{symbol}_trail_sl"
                trail_peak_key = f"{symbol}_trail_peak"
                trail_tick_key = f"{symbol}_trail_just_activated"

                if trail_enabled and not self.alerts_history.get(trail_key, False):
                    # Activacion 1:1 -> logica PURA en sl_tp.trail_activacion()
                    # (devuelve None=no activa / {"api","sl"}=como ejecutarla)
                    entry_data = self.trade_entries.get(symbol, {})
                    original_sl = entry_data.get("sl_price") if entry_data else None
                    act = sl_tp.trail_activacion(side, entry, original_sl, mark)

                    if act is not None:
                        self.alerts_history[trail_key] = True
                        self.alerts_history[trail_sl_key] = act["sl"]
                        self.alerts_history[trail_peak_key] = mark
                        self.alerts_history[trail_tick_key] = True
                        if act["api"]:
                            if await self._update_stop_loss(symbol, side, act["sl"]):
                                self.trail_counts[symbol] = 0
                                log.info(f"{symbol} Trailing 1:1 activado. "
                                         f"SL={act['sl']:.6f}")
                                await self.send_telegram(
                                    f"*{symbol}* Trailing 1:1 activado")
                        else:
                            # Marca activo pero espera al proximo tick
                            self.trail_counts[symbol] = 0
                            log.info(f"{symbol} Trailing 1:1 (SL se ajusta "
                                     f"en proximo tick)")
                            await self.send_telegram(
                                f"*{symbol}* Trailing 1:1 activado")

                # -----------------------------------------------------
                # [FIX-4] TRAILING STOP: SL anclado al PICO con distancia
                # fija. Antes trailing_dist_pct (0.35%) estaba declarado en
                # CONFIG pero NUNCA se usaba, y el SL subia un % multiplicado
                # sobre si mismo (0.3% por extremo) desacoplandose del precio
                # -> el trailing podia quedarse muy detras del pico.
                #   trailing_dist_pct (0.35%) = distancia real SL <-> pico
                #   trailing_step_pct (0.3%)  = mejora MINIMA exigida antes
                #       de llamar a la API (evita spam de ordenes y 429)
                # Invariantes:
                #   * el pico SIEMPRE se actualiza (aunque falle la API)
                #   * el SL solo se mueve si MEJORA >= trailing_step_pct
                #     (monotonia: jamas empeora el stop)
                #   * el SL jamas cruza el precio actual
                # (logica PURA -> sl_tp.py [3] TRAILING: objetivo + gate;
                #  aqui SOLO se ejecuta la decision: API/estado/logs)
                # -----------------------------------------------------
                if trail_enabled and self.alerts_history.get(trail_key, False):
                    current_trail_sl = float(
                        self.alerts_history.get(trail_sl_key, 0) or 0)
                    prev_peak = self.alerts_history.get(trail_peak_key, mark)
                    self.alerts_history.pop(trail_tick_key, None)

                    if side == "long":
                        if mark > prev_peak:  # nuevo maximo del precio
                            objetivo = sl_tp.trail_objetivo("long", mark,
                                                            self.cfg)
                            if sl_tp.trail_debe_mover("long", objetivo,
                                                      current_trail_sl, mark,
                                                      self.cfg):
                                if await self._update_stop_loss(symbol, side,
                                                                objetivo):
                                    self.alerts_history[trail_sl_key] = objetivo
                                    self.trail_counts[symbol] = \
                                        self.trail_counts.get(symbol, 0) + 1
                                    log.info(f"{symbol} Trail -> {objetivo:.6f} "
                                             f"(pico {mark:.6f})")
                            self.alerts_history[trail_peak_key] = mark
                    else:
                        if mark < prev_peak:  # nuevo minimo del precio
                            objetivo = sl_tp.trail_objetivo("short", mark,
                                                            self.cfg)
                            if sl_tp.trail_debe_mover("short", objetivo,
                                                      current_trail_sl, mark,
                                                      self.cfg):
                                if await self._update_stop_loss(symbol, side,
                                                                objetivo):
                                    self.alerts_history[trail_sl_key] = objetivo
                                    self.trail_counts[symbol] = \
                                        self.trail_counts.get(symbol, 0) + 1
                                    log.info(f"{symbol} Trail -> {objetivo:.6f} "
                                             f"(pico {mark:.6f})")
                            self.alerts_history[trail_peak_key] = mark

        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA, "manage_positions: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA, "manage_positions: error de red.", logging.WARNING)
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"manage_positions: {e}", logging.ERROR)
        except Exception as e:
            flag("MANAGE-UNEXPECTED", origen_de_excepcion(e),
                 f"manage_positions: {e}", logging.ERROR)

    async def close_position(self, symbol: str) -> bool:
        """[8.6d] Cierra la posicion completa (TP2, manual/kill-switch)."""
        try:
            await self._exch_call("close_position", symbol)
            await self._cancel_exchange_tps(symbol)   # [TPO-3] sin TP colgando
            log.info(f"{symbol} cerrada (cierre total).")
            return True
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"close_position {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA, f"close_position {symbol}: {e}", logging.ERROR)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"close_position {symbol}: error de red.", logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"close_position {symbol}: {e}", logging.ERROR)
            return False
        except Exception as e:
            flag("CLOSE-UNEXPECTED", origen_de_excepcion(e),
                 f"Cerrando {symbol}: {e}", logging.ERROR)
            return False

    async def _close_partial(self, symbol: str, side: str, qty: float,
                             frac: float) -> Optional[str]:
        """
        [8.6d-bis] Cierra UNA FRACCION de la posicion (TP1) via orden MARKET
        reduce-only. Documentacion Bitget V2 (Place-Order):
          modo una via  -> side contrario + reduceOnly=yes
                           (tradeSide se ignora)
          modo hedge    -> side contrario + tradeSide=close
                           (reduceOnly solo aplica a modo una via)
          => se envian AMBOS parametros: cada modo usa su suyo e ignora el
             otro, cubriendo ambos sin tener que detectar el modo de la cuenta.

        [FIX-H2] Cuantizacion al step con FLOOR (identica a
        sl_tp.split_tp_qty, con la misma tolerancia fp), para que el cierre
        bot-side sea EXACTAMENTE el que habria puesto el exchange.
          ANTES usaba round(): con qty=0.003/step=0.001 cerraba 0.002 (67%)
          y con steps=0 (qty=step, p.ej. XAUT 0.01) cerraba el 100% de la
          posicion mientras el log decia "TP1 PARCIAL 50%".

        Devuelve:
          "parcial" -> se cerro close_qty (< qty), alineado al step por ABAJO.
          "total"   -> NO existe fraccion valida 0 < close_qty < qty al step
                       (qty < 2 steps o frac>=1): se cierra TODO y el llamador
                       lo debe declarar como CIERRE TOTAL en TP1
                       (flag TP1-NOSPLIT + tp2_done).
          None      -> la orden fallo (ya flageada en los except).
        """
        try:
            market = await self._exch_call("market", symbol)
            precision = market["precision"]["amount"]
            step = market["limits"]["amount"]["min"] or (10 ** -precision)
            # floor al step (MISMA formula/tolerancia que split_tp_qty)
            steps = int(math.floor((qty * frac) / step + 1e-9))
            close_qty = round(steps * step, 12)

            if close_qty < step or close_qty >= qty:
                # [FIX-H2] qty no admite partida: cierre TOTAL declarado.
                flag("TP1-NOSPLIT", ORIGEN_CODIGO,
                     f"{symbol}: qty={qty} frac={frac} step={step} -> sin "
                     f"fraccion valida (0<{step}<=close<{qty}); TP1 ejecuta "
                     f"CIERRE TOTAL.", logging.WARNING)
                close_qty = qty
                modo = "total"
            else:
                modo = "parcial"

            close_side = "sell" if side == "long" else "buy"
            params = {
                "marginCoin": "USDT",
                "reduceOnly": True,
                "tradeSide": "close",
            }
            await self._exch_call("create_order", symbol, "market",
                                  close_side, close_qty, None, params)
            log.info(f"{symbol} cierre {modo}: {close_qty} "
                     f"({close_qty / qty * 100:.0f}% de {qty}) "
                     f"lado={close_side}")
            return modo
        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_close_partial {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            flag("400", ORIGEN_BIBLIOTECA, f"_close_partial {symbol}: {e}",
                 logging.ERROR)
            return False
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_close_partial {symbol}: error de red.", logging.WARNING)
            return False
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA, f"_close_partial {symbol}: {e}",
                 logging.ERROR)
            return False
        except Exception as e:
            flag("PARTIAL-UNEXPECTED", origen_de_excepcion(e),
                 f"_close_partial {symbol}: {e}", logging.ERROR)
            return False

    async def _process_closed_position(self, sym: str):
        """
        [8.6e] Al cerrarse una posicion: calcula PnL/fees reales desde el
        historial de trades, alerta a Telegram, registra en CSV y armamos el
        monitor de SL prematuro (¿el TP se habria alcanzado tras el SL?).
        """
        try:
            await asyncio.sleep(2)  # espera a que Bitget consolide el fill
            await self._cancel_exchange_tps(sym)  # [TPO-3] limpiar TP residuales
            trades = await self._exch_call("fetch_my_trades", sym, limit=20)
            if not trades:
                return

            # Localizar el trade de cierre (profit != 0)
            last_closing = None
            for t in reversed(trades):
                if float(t["info"].get("profit", 0)) != 0:
                    last_closing = t
                    break
            if not last_closing:
                return

            # [TPO-2] Sumar PnL y fees de TODOS los fills de ESTA posicion
            # (entrada + TP1 parcial + cierre final). Antes se sumaba solo
            # la ultima orden de cierre, con TP1 el PnL saldria incompleto.
            # Filtro por entry_time para no colar fills de la posicion
            # anterior (mismo simbolo, cooldown 1h).
            entry = self.trade_entries.get(sym) or {}
            entry_ms = None
            if entry.get("entry_time"):
                try:
                    entry_ms = int(datetime.fromisoformat(
                        entry["entry_time"]).timestamp() * 1000)
                except (ValueError, TypeError):
                    entry_ms = None
            trade_pnl, trade_fees = 0.0, 0.0
            for t in trades:
                if entry_ms is not None and int(t.get("timestamp", 0)) < entry_ms:
                    continue
                trade_pnl += float(t["info"].get("profit", 0))
                if t.get("fee"):
                    trade_fees += abs(float(t["fee"].get("cost", 0)))

            net = trade_pnl - trade_fees
            status = "TP" if trade_pnl > 0 else ("SL" if trade_pnl < 0 else "BE")
            reason = "tp" if trade_pnl > 0 else ("sl" if trade_pnl < 0 else "be")

            await self.send_telegram(
                f"*{sym} CERRADA*\nPnL: {net:.2f} USDT ({status})\n"
                f"Fees: -{trade_fees:.2f}")
            self.record_trade_result(net)

            # Volcar la entrada al CSV de trades
            self.trade_entries.pop(sym, None)
            if entry:
                exit_px = float(last_closing.get("price", 0))
                entry_dt = (datetime.fromisoformat(entry["entry_time"])
                            if isinstance(entry["entry_time"], str)
                            else entry["entry_time"])
                self._save_trade_csv(entry, exit_px, trade_pnl, trade_fees,
                                     net, status, reason, entry_dt)
            await self._save_trade_entries()

        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"_process_closed_position {sym}: rate limit.", logging.WARNING)
        except (NetworkError, RequestTimeout):
            flag("NET", ORIGEN_BIBLIOTECA,
                 f"_process_closed_position {sym}: error de red.", logging.WARNING)
        except ExchangeError as e:
            flag("500", ORIGEN_BIBLIOTECA,
                 f"_process_closed_position {sym}: {e}", logging.ERROR)
        except Exception as e:
            flag("PROC-UNEXPECTED", origen_de_excepcion(e),
                 f"Procesando cierre de {sym}: {e}", logging.ERROR)

    def _cleanup_symbol(self, sym: str):
        """[8.6f] Borra el estado en memoria de un simbolo ya cerrado."""
        self.peak_prices.pop(sym, None)
        self.adverse_prices.pop(sym, None)
        self.alerts_history.pop(f"{sym}_be", None)
        self.alerts_history.pop(f"{sym}_be_price", None)
        self.alerts_history.pop(f"{sym}_trail_active", None)
        self.alerts_history.pop(f"{sym}_trail_sl", None)
        self.alerts_history.pop(f"{sym}_trail_peak", None)
        self.trail_counts.pop(sym, None)
        self.session_active.discard(sym)
        self._tp_recon_last.pop(sym, None)   # [TPO-3]

    # -----------------------------------------------------------------
    # [8.7] COOLDOWN POR PERDIDAS CONSECUTIVAS (riesgo global)
    # -----------------------------------------------------------------
    def record_trade_result(self, net_pnl: float):
        """
        [8.7a] Registra el resultado de un trade cerrado:
          - Clasico: N perdidas consecutivas -> pausa cooldown_hours.
          - Rolling: si el PnL acumulado de los ultimos N trades < umbral,
            pausa tambien (detecta rachas malas sin ser consecutivas).
        """
        if net_pnl >= 0:
            if self.consecutive_losses > 0:
                log.info(f"Trade ganador. Perdidas reseteadas "
                         f"({self.consecutive_losses} -> 0)")
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            log.info(f"Perdida consecutiva #{self.consecutive_losses}")
            if self.consecutive_losses >= self.cfg["max_consecutive_losses"]:
                self.cooldown_until = time.time() + self.cfg["cooldown_hours"] * 3600
                flag("COOLDOWN-SEQ", ORIGEN_CODIGO,
                     f"{self.cfg['max_consecutive_losses']} perdidas consecutivas. "
                     f"Pausa {self.cfg['cooldown_hours']}h.", logging.WARNING)

        # Cooldown rolling por PnL negativo acumulado
        window = self.cfg["cooldown_rolling_window"]
        self.rolling_pnl.append(net_pnl)
        if len(self.rolling_pnl) > window:
            self.rolling_pnl = self.rolling_pnl[-window:]
        if len(self.rolling_pnl) >= window:
            rolling_sum = sum(self.rolling_pnl)
            threshold = self.cfg["cooldown_loss_threshold"]
            if rolling_sum < threshold and self.cooldown_until is None:
                self.cooldown_until = time.time() + self.cfg["cooldown_hours"] * 3600
                flag("COOLDOWN-ROLL", ORIGEN_CODIGO,
                     f"Cooldown ROLLING: PnL ultimos {window} trades = "
                     f"{rolling_sum:+.4f} (umbral {threshold}). "
                     f"Pausa {self.cfg['cooldown_hours']}h.", logging.WARNING)

    def is_on_cooldown(self) -> bool:
        """[8.7b] ¿Estamos en pausa? Expira el cooldown automaticamente."""
        if self.cooldown_until is None:
            return False
        if time.time() >= self.cooldown_until:
            log.info("Cooldown finalizado. Reanudando.")
            self.cooldown_until = None
            self.consecutive_losses = 0
            return False
        remaining = (self.cooldown_until - time.time()) / 60
        log.debug(f"En pausa. Faltan {remaining:.0f} min.")
        return True

    # -----------------------------------------------------------------
    # [8.8] PERSISTENCIA - entradas abiertas (JSON) y trades (CSV)
    # -----------------------------------------------------------------
    async def _save_trade_entries(self):
        """[8.8a] Guarda las entradas abiertas a trade_entries.json (thread)."""
        def _sync():
            data = {sym: e.copy() for sym, e in self.trade_entries.items()}
            with open(self.trade_entries_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            await asyncio.to_thread(_sync)
        except Exception as e:
            flag("SAVE-JSON", origen_de_excepcion(e),
                 f"_save_trade_entries: {e}", logging.ERROR)

    async def _load_trade_entries(self):
        """[8.8b] Recupera entradas abiertas tras un reinicio (persistencia)."""
        def _sync():
            if not os.path.exists(self.trade_entries_path):
                return {}
            with open(self.trade_entries_path, "r", encoding="utf-8") as f:
                return json.load(f)
        try:
            data = await asyncio.to_thread(_sync)
            if data:
                self.trade_entries.update(data)
                log.info(f"Cargadas {len(data)} entradas desde trade_entries.json")
        except Exception as ex:
            flag("LOAD-JSON", origen_de_excepcion(ex),
                 f"_load_trade_entries: {ex}", logging.ERROR)

    def _save_trade_csv(self, entry, exit_price, raw_pnl, fees, net, status,
                        reason, entry_dt: datetime):
        """[8.8c] Append de un trade cerrado a trades.csv (metricas futuras)."""
        now = datetime.now()
        duration = (now - entry_dt).total_seconds() / 3600
        ep = entry["entry_price"]
        sym = entry["symbol"]

        row = {
            "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym,
            "side": entry["side"],
            "entry_price": ep,
            "exit_price": exit_price,
            "sl_price": entry["sl_price"],
            # [TPO-2] tp_price del CSV = TP2 (objetivo final); TP1 en
            # tp1_price del trade_entries.json (mismas cabeceras CSV).
            "tp_price": entry.get("tp2_price", entry.get("tp_price", 0.0)),
            "sl_pct": round(abs(ep - entry["sl_price"]) / ep * 100, 2),
            "tp_pct": round(abs(entry.get("tp2_price",
                                          entry.get("tp_price", ep)) - ep)
                            / ep * 100, 2),
            "quantity": entry["quantity"],
            "balance_before": round(entry["balance_before"], 2),
            "balance_after": round(entry["balance_before"] + net, 2),
            "pnl": round(raw_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net, 2),
            "status": status,
            "duration_hours": round(duration, 2),
            "close_reason": reason,
            "be_triggered": 1 if self.alerts_history.get(f"{sym}_be", False) else 0,
            "be_price": round(self.alerts_history.get(f"{sym}_be_price", 0), 4),
            "trail_count": self.trail_counts.get(sym, 0),
            "trail_peak_price": round(self.peak_prices.get(sym, ep), 4),
            "trail_final_sl": round(
                self.alerts_history.get(f"{sym}_trail_sl", entry["sl_price"]), 4),
            "entry_weekday": entry_dt.weekday(),
            "entry_hour": entry_dt.hour,
            "size_usdt": entry.get("size_usdt", 0),
            "risk_pct": entry.get("risk_pct", 0),
            "max_favorable_pct": round(
                abs(self.peak_prices.get(sym, ep) - ep) / ep * 100, 2),
            "max_adverse_pct": round(
                abs(self.adverse_prices.get(sym, ep) - ep) / ep * 100, 2),
        }
        write_header = not os.path.exists(self.trades_csv)
        try:
            with open(self.trades_csv, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.TRADE_CSV_HEADERS)
                if write_header:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            flag("CSV-WRITE", origen_de_excepcion(e),
                 f"_save_trade_csv: {e}", logging.ERROR)

    # -----------------------------------------------------------------
    # [8.9] UTILIDADES de consulta de posiciones
    # -----------------------------------------------------------------
    async def get_open_symbols(self) -> set:
        """[8.9a] Simbolos con posicion viva ahora mismo."""
        try:
            positions = await self._exch_call("fetch_positions")
            return {p["symbol"] for p in positions if float(p["contracts"]) > 0}
        except Exception as e:
            flag("POS-LIST", origen_de_excepcion(e),
                 f"get_open_symbols: {e}", logging.WARNING)
            return set()

    async def get_position_count(self) -> int:
        """[8.9b] Nº de posiciones abiertas."""
        return len(await self.get_open_symbols())

    async def can_open(self) -> bool:
        """[8.9c] ¿Queda hueco bajo max_open_positions?"""
        return (await self.get_position_count()) < self.cfg["max_open_positions"]

    def en_cooldown_simbolo(self, symbol: str) -> bool:
        """
        [8.9d] [FIX-3] ¿Esta el simbolo en su pausa de 1h tras cerrar?
          Antes self.cooldowns se ESCRIBIA en manage_positions() pero nunca
          se leia: el cooldown por simbolo era codigo muerto y se podia
          re-entrar en el mismo minuto que salio (revenge trading).
          Poda las claves vencidas para que el dict no crezca en RAM.
        """
        hasta = self.cooldowns.get(symbol)
        if hasta is None:
            return False
        if time.time() >= hasta:
            self.cooldowns.pop(symbol, None)   # pausa vencida -> limpiar
            return False
        return True


# =============================================================================
# [9] ESTRATEGIA ACTIVA - SignalVWAP (traduccion Python del Pine v5 adjunto)
#      indicator SignalVWAP Clean: EMAs 9/14/50/100 + VWAP anclado +
#      estructura de pivotes (BOS/CHOCH) + Canal de Regresion Logaritmica.
# =============================================================================
class SignalVWAP:
    """
    [9.0] Motor puro de la senal (sin acceso a red: solo pandas/numpy).
      Metodos:
        [9.1] _emas()          -> EMAs 9/14/50/100 (ta.ema de TradingView)
        [9.2] _vwap()          -> VWAP con ancla configurable (Session/Week/...)
        [9.3] _estructura()    -> pivotes de leg + cruces de estructura
        [9.4] _canal()         -> regresion logaritmica + bandas +-N desv
        [9.5] evaluar()        -> orquestador: devuelven todas las series
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg

    # -----------------------------------------------------------------
    # [9.1] EMAS del indicador: shortest=9, short=14, longer=50, longest=100
    # -----------------------------------------------------------------
    def _emas(self, close: pd.Series) -> dict:
        """[9.1] Cuatro EMAs con inicializacion SMA (identica a ta.ema)."""
        return {
            "fast": MathPreservados.ema_tv(close, self.cfg["ema_fast"]),   # EMA 20
            "mid": MathPreservados.ema_tv(close, self.cfg["ema_mid"]),     # EMA 50
            "slow": MathPreservados.ema_tv(close, self.cfg["ema_slow"]),   # EMA 100
            "cross": MathPreservados.ema_tv(close, self.cfg["ema_cross"]),  # EMA 200
        }

    # -----------------------------------------------------------------
    # [9.2] VWAP ANCLADO - determina el cambio de periodo y acumula
    # -----------------------------------------------------------------
    def _period_key(self, ts_ms: np.ndarray, anchor: str) -> np.ndarray:
        """
        [9.2a] Clave de periodo por barra: cambia cuando empieza periodo nuevo.
          Session -> dia UTC (+ offset configurable)
          Week    -> semana ISO       Month -> mes
          Quarter -> trimestre        Year  -> anyo
          Decade  -> anyo divisible entre 10 al cambiar de anyo
          Century -> anyo divisible entre 100 al cambiar de anyo
          Earnings/Dividends/Splits -> NO aplica a crypto: sin reinicio
                                       (flag CODIGO-014, VWAP continuo)
        """
        off = self.cfg.get("vwap_anchor_offset_hours", 0) * 3600
        sec = ts_ms / 1000.0 + off
        if anchor == "Session":
            return (sec // 86400).astype(np.int64)
        # pd.to_datetime sobre ndarray devuelve DatetimeIndex (NO tiene .dt):
        # se envuelve en Series para poder usar el accessor .dt.
        dt = pd.Series(pd.to_datetime(ts_ms, unit="ms", utc=True))
        if anchor == "Week":
            iso = dt.dt.isocalendar()
            return (iso.year.astype(np.int64) * 100 + iso.week.astype(np.int64)).values
        if anchor == "Month":
            return (dt.dt.year.astype(np.int64) * 100 + dt.dt.month.astype(np.int64)).values
        if anchor == "Quarter":
            q = ((dt.dt.month - 1) // 3 + 1).astype(np.int64)
            return (dt.dt.year.astype(np.int64) * 10 + q).values
        if anchor in ("Year", "Decade", "Century"):
            year = dt.dt.year.astype(np.int64).values
            if anchor == "Year":
                return year
            # anyo normalizado al inicio del decenio/siglo (cambia 1 vez)
            step = 10 if anchor == "Decade" else 100
            return (year // step).astype(np.int64)
        # Earnings/Dividends/Splits: sin datos fundamentales en crypto
        flag("CODIGO-014", ORIGEN_CODIGO,
             f"Ancla '{anchor}' no aplica a crypto: VWAP continuo (sin reinicio).",
             logging.WARNING)
        return np.zeros(len(ts_ms), dtype=np.int64)

    def _vwap(self, df: pd.DataFrame) -> np.ndarray:
        """
        [9.2b] VWAP anclado (ta.vwap de Pine):
          VWAP[i] = cumsum(hlc3 * vol)[desde inicio de periodo] /
                    cumsum(vol)[...]
        Reinicia en cada cambio de periodo detectado por _period_key().
        Si el volumen acumulado es 0 -> NaN (flag DATOS-001).
        """
        h = df["high"].values.astype(np.float64)
        l = df["low"].values.astype(np.float64)
        c = df["close"].values.astype(np.float64)
        v = df["volume"].values.astype(np.float64)
        n = len(df)

        if n == 0:
            return np.zeros(0, dtype=np.float64)

        if not np.any(v > 0):
            flag("DATOS-001", ORIGEN_DATOS,
                 "Volumen 0 en todas las velas: VWAP no calculable (NaN).",
                 logging.WARNING)
            return np.full(n, np.nan, dtype=np.float64)

        hlc3 = (h + l + c) / 3.0
        key = self._period_key(df["timestamp"].values, self.cfg["vwap_anchor"])

        vwap = np.empty(n, dtype=np.float64)
        cum_pv = 0.0
        cum_v = 0.0
        for i in range(n):
            if i == 0 or key[i] != key[i - 1]:
                cum_pv = hlc3[i] * v[i]   # reinicio del acumulado
                cum_v = v[i]
            else:
                cum_pv += hlc3[i] * v[i]
                cum_v += v[i]
            if cum_v > 0:
                vwap[i] = cum_pv / cum_v
            else:
                vwap[i] = np.nan
                if i > 0:
                    flag("DATOS-001", ORIGEN_DATOS,
                         f"Volumen acumulado 0 en barra {i}: VWAP=NaN.",
                         logging.DEBUG)
        return vwap

    # -----------------------------------------------------------------
    # [9.3] ESTRUCTURA DE PIVOTES - legs, swingHigh/Low, cruces (BOS/CHOCH)
    # -----------------------------------------------------------------
    @staticmethod
    def _nuevas_patas(arr: np.ndarray, sz: int, es_high: bool) -> np.ndarray:
        """
        [9.3a] Deteccion de "nueva pata" (Pine leg()):
          newLegHigh[i] = high[i-sz] > max(high[i-sz+1 .. i])
          newLegLow [i] = low [i-sz] < min(low [i-sz+1 .. i])
        O sea: la barra hace sz es el extremo confirmado respecto a las sz
        barras posteriores.  Requiere i >= sz.
        """
        n = len(arr)
        out = np.zeros(n, dtype=bool)
        if n <= sz:
            return out
        roll = (pd.Series(arr).rolling(sz, min_periods=sz).max().values
                if es_high else
                pd.Series(arr).rolling(sz, min_periods=sz).min().values)
        idx = np.arange(sz, n)
        if es_high:
            out[idx] = arr[idx - sz] > roll[idx]
        else:
            out[idx] = arr[idx - sz] < roll[idx]
        return out

    def _estructura(self, df: pd.DataFrame) -> dict:
        """
        [9.3b] Maquina de estados de la estructura (Pine secciones 2 y 4):
          - Dos series de "leg" (swing sz=50 y internal sz=5).
          - Al confirmarse un extremo se actualiza el pivot correspondiente
            (currentLevel, lastLevel, crossed=False, barIndex de la pivote).
          - displayStructure(): si close CRUZA el nivel del pivot alto por
            encima -> estructura alcista (BULLISH); por debajo del pivot bajo
            -> bajista (BEARISH). Ese cruce marca el inicio del canal.
          NOTA [CODIGO-001]: se asume estado de leg independiente por tipo
          (swing vs internal). [CODIGO-002]: la estructura internal se calcula
          pero displayStructure() solo corre para swing (como en Pine).
        """
        n = len(df)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        close = df["close"].values.astype(np.float64)
        ts = df["timestamp"].values

        sz_swing = self.cfg["swings_length"]      # 50
        sz_int = self.cfg["internal_length"]       # 5

        # Patas nuevas por tipo de estructura
        nh_s, nl_s = self._nuevas_patas(high, sz_swing, True), \
            self._nuevas_patas(low, sz_swing, False)
        nh_i, nl_i = self._nuevas_patas(high, sz_int, True), \
            self._nuevas_patas(low, sz_int, False)

        # Estado de pivotes por tipo: [cur_level, last_level, crossed, barIndex]
        piv = {
            "swingHigh": [np.nan, np.nan, False, -1],
            "swingLow": [np.nan, np.nan, False, -1],
            "internalHigh": [np.nan, np.nan, False, -1],
            "internalLow": [np.nan, np.nan, False, -1],
        }
        leg = {"swing": 0, "internal": 0}   # valor actual de leg (var en Pine)

        # Series de salida (para crossovers y dibujo)
        lvl_h = np.full(n, np.nan)   # nivel del pivot alto vigente
        lvl_l = np.full(n, np.nan)   # nivel del pivot bajo vigente
        # [11.4] Ultimo pivot SWING registrado en cada barra (para el SL:
        # "el pivot que toco la base/tope del canal")
        piv_hi_lvl = np.full(n, np.nan)            # nivel del pivot ALTO
        piv_hi_bar = np.full(n, -1, dtype=np.int64)  # barra del pivot ALTO
        piv_lo_lvl = np.full(n, np.nan)            # nivel del pivot BAJO
        piv_lo_bar = np.full(n, -1, dtype=np.int64)  # barra del pivot BAJO
        cruce_alcista = np.zeros(n, dtype=bool)   # close > nivel alto (BOS up)
        cruce_bajista = np.zeros(n, dtype=bool)   # close < nivel bajo  (BOS down)
        canal_start = np.full(n, -1, dtype=np.int64)  # barIndex de inicio canal
        bias = np.zeros(n, dtype=np.int8)         # +1 alcista / -1 bajista / 0

        start_actual = -1
        bias_actual = 0

        for t in range(n):
            # --- (a) actualizar leg y pivotes de SWING (sz=50) ---
            if t >= sz_swing:
                prev = leg["swing"]
                if nh_s[t]:
                    leg["swing"] = 0
                elif nl_s[t]:
                    leg["swing"] = 1
                if t > 0 and leg["swing"] != prev:
                    pz = t - sz_swing   # barra de la pivote confirmada
                    if leg["swing"] == 1:
                        # empieza pata alcista -> pivot BAJISTA confirmado
                        p = piv["swingLow"]
                        p[1], p[0] = p[0], low[pz]
                        p[2], p[3] = False, pz
                    else:
                        # empieza pata bajista -> pivot ALTA confirmado
                        p = piv["swingHigh"]
                        p[1], p[0] = p[0], high[pz]
                        p[2], p[3] = False, pz

            # --- (b) actualizar leg y pivotes de INTERNAL (sz=5) ---
            if t >= sz_int:
                prev = leg["internal"]
                if nh_i[t]:
                    leg["internal"] = 0
                elif nl_i[t]:
                    leg["internal"] = 1
                if t > 0 and leg["internal"] != prev:
                    pz = t - sz_int
                    if leg["internal"] == 1:
                        p = piv["internalLow"]
                        p[1], p[0] = p[0], low[pz]
                        p[2], p[3] = False, pz
                    else:
                        p = piv["internalHigh"]
                        p[1], p[0] = p[0], high[pz]
                        p[2], p[3] = False, pz

            # --- (c) displayStructure() SOLO para swing (internal=false) ---
            lvl_h[t] = piv["swingHigh"][0]
            lvl_l[t] = piv["swingLow"][0]
            # Estado del pivot vigente en esta barra (para el SL por pivot)
            piv_hi_lvl[t] = piv["swingHigh"][0]
            piv_hi_bar[t] = piv["swingHigh"][3]
            piv_lo_lvl[t] = piv["swingLow"][0]
            piv_lo_bar[t] = piv["swingLow"][3]
            # Ta.crossover(close, nivel_alto): close cruza hacia ARRIBA
            if (t > 0 and np.isfinite(lvl_h[t]) and np.isfinite(lvl_h[t - 1])
                    and not piv["swingHigh"][2]
                    and close[t - 1] <= lvl_h[t - 1] and close[t] > lvl_h[t]):
                piv["swingHigh"][2] = True          # ya cruzado
                bias_actual = +1                     # BULLISH
                cruce_alcista[t] = True
                start_actual = piv["swingHigh"][3]   # barIndex de la pivote
            # Ta.crossunder(close, nivel_bajo): close cruza hacia ABAJO.
            # Pine usa dos 'if' independientes (no else-if): se reproduce
            # aqui aunque solo pueda dispararse uno por vela.
            if (t > 0 and np.isfinite(lvl_l[t]) and np.isfinite(lvl_l[t - 1])
                    and not piv["swingLow"][2]
                    and close[t - 1] >= lvl_l[t - 1] and close[t] < lvl_l[t]):
                piv["swingLow"][2] = True
                bias_actual = -1                     # BEARISH
                cruce_bajista[t] = True
                start_actual = piv["swingLow"][3]

            canal_start[t] = start_actual
            bias[t] = bias_actual

        return {
            "lvl_h": lvl_h, "lvl_l": lvl_l,
            "cruce_alcista": cruce_alcista, "cruce_bajista": cruce_bajista,
            "canal_start": canal_start, "bias": bias,
            "piv_hi_lvl": piv_hi_lvl, "piv_hi_bar": piv_hi_bar,
            "piv_lo_lvl": piv_lo_lvl, "piv_lo_bar": piv_lo_bar,
        }

    # -----------------------------------------------------------------
    # [9.4] CANAL DE REGRESION LOGARITMICA (bandas +-mult*desviacion)
    # -----------------------------------------------------------------
    def _canal(self, df: pd.DataFrame, canal_start: np.ndarray) -> dict:
        """
        [9.4] Regresion lineal sobre log(precio) de la ventana desde la
        pivote que inicio la estructura hasta la barra actual:
          y = ln(close), x = 0..m-1  ->  pendiente e intercepto (min. cuadrados)
          desviacion tipica de los residuos (m-1)
          banda superior = exp(pred) * exp(mult * std)
          banda inferior = exp(pred) / exp(mult * std)
        Optimizacion: sums incremental O(1)/barra (O(n) total por simbolo);
        reconstruccion O(m) solo cuando cambia la pivote de inicio.
        Salidas: upper/lower por barra + segmento final para dibujar.
        """
        n = len(df)
        close = df["close"].values.astype(np.float64)
        max_hist = self.cfg["channel_max_history"]      # 4999
        dev_mult = self.cfg["channel_dev_mult"]          # 2.0

        upper = np.full(n, np.nan, dtype=np.float64)
        lower = np.full(n, np.nan, dtype=np.float64)
        pendientes = np.full(n, np.nan, dtype=np.float64)

        # sums acumulados de la ventana activa
        win_start = -1        # primera barra incluida (x=0)
        m = 0                 # tamano de la ventana
        sumY = sumY2 = sumXY = 0.0

        for t in range(n):
            cs = int(canal_start[t])
            if cs < 0:
                continue                       # aun no hay estructura
            period = min(t - cs + 1, max_hist)  # USE_PERIOD_INICIO
            if period < 2 or t < period - 1:
                continue                       # canCalculateInicio = false
            w_start = t - period + 1

            y_new = math.log(close[t]) if close[t] > 0 else np.nan
            if not np.isfinite(y_new):
                flag("DATOS-003", ORIGEN_DATOS,
                     f"close<=0 en barra {t}: canal no calculable.", logging.WARNING)
                continue

            if w_start != win_start:
                # Reconstruir ventana desde cero (nueva pivote o primer calculo)
                win_start = w_start
                ys = np.log(close[w_start:t + 1])
                m = len(ys)
                xs = np.arange(m, dtype=np.float64)
                sumY = float(ys.sum())
                sumY2 = float((ys * ys).sum())
                sumXY = float((xs * ys).sum())
            else:
                # Extender ventana una barra a la derecha (x = m -> m viejo)
                sumXY += m * y_new
                sumY += y_new
                sumY2 += y_new * y_new
                m += 1

            if m < 2:
                continue
            # Formulas cerradas de sumX y sumXX para x = 0..m-1
            sumX = m * (m - 1) / 2.0
            sumXX = (m - 1) * m * (2 * m - 1) / 6.0
            denom = m * sumXX - sumX * sumX
            if denom == 0:
                flag("CODIGO-007", ORIGEN_CODIGO,
                     f"denominador=0 en regresion barra {t}.", logging.WARNING)
                continue

            pend = (m * sumXY - sumX * sumY) / denom           # slope
            intercept = (sumY - pend * sumX) / m               # intercept

            # SSE de residuos desde las sums (evita re-loopar la ventana):
            # SSE = sumY2 - 2*b0*sumY - 2*b1*sumXY + m*b0^2 + 2*b0*b1*sumX + b1^2*sumXX
            sse = (sumY2 - 2 * intercept * sumY - 2 * pend * sumXY
                   + m * intercept * intercept
                   + 2 * intercept * pend * sumX
                   + pend * pend * sumXX)
            if sse < 0:
                if sse < -1e-6:
                    flag("CODIGO-008", ORIGEN_CODIGO,
                         f"SSE negativo ({sse:.3e}) barra {t}; trunco a 0.",
                         logging.DEBUG)
                sse = 0.0
            std = math.sqrt(sse / (m - 1))

            try:
                price_first = math.exp(intercept)                # x=0 (pivote)
                price_last = math.exp(intercept + pend * (m - 1))  # x=m-1 (hoy)
                mult = math.exp(dev_mult * std)
            except OverflowError:
                flag("CODIGO-012", ORIGEN_CODIGO,
                     f"Overflow en exp() del canal barra {t}; vela omitida.",
                     logging.WARNING)
                continue

            upper[t] = price_last * mult
            lower[t] = price_last / mult
            pendientes[t] = pend

        # Segmento final (ultima barra valida) para dibujar la recta
        seg = {"ok": False}
        last = n - 1
        if np.isfinite(upper[last]):
            cs = int(canal_start[last])
            period = min(last - cs + 1, max_hist)
            # Recalcular valor en la pivote (x=0) con el estado final:
            w_start = last - period + 1
            ys = np.log(close[w_start:last + 1])
            m2 = len(ys)
            xs2 = np.arange(m2, dtype=np.float64)
            sX = float(xs2.sum())
            sXX = float((xs2 * xs2).sum())
            sY = float(ys.sum())
            sXY = float((xs2 * ys).sum())
            den = m2 * sXX - sX * sX
            if den != 0:
                p2 = (m2 * sXY - sX * sY) / den
                b2 = (sY - p2 * sX) / m2
                try:
                    pf = math.exp(b2)
                    pl = math.exp(b2 + p2 * (m2 - 1))
                    # Pine: sumResid -> stdDev = sqrt(sumResid/(n-1)),
                    #       mult = exp(devMultiplier * stdDev).
                    # [CODIGO-015] BUG CORREGIDO: antes era
                    #   exp(dev * sqrt(SSE) / (m-1))  (banda ~sqrt(m-1)x mas
                    #   estrecha); ahora identico a Pine:
                    #   exp(dev * sqrt(SSE/(m-1))).
                    sse2 = max(0.0, float((ys * ys).sum()) - 2 * b2 * sY
                               - 2 * p2 * sXY + m2 * b2 * b2
                               + 2 * b2 * p2 * sX + p2 * p2 * sXX)
                    mu = math.exp(dev_mult * math.sqrt(sse2 / (m2 - 1)))
                    seg = {
                        "ok": True,
                        "x0": w_start, "x1": last,          # barras inicio/fin
                        "y_up0": pf * mu, "y_up1": pl * mu,  # recta superior
                        "y_lo0": pf / mu, "y_lo1": pl / mu,  # recta inferior
                        "trend": (1 if p2 > 0 else (-1 if p2 < 0 else 0)),
                        "slope": p2,
                    }
                except OverflowError:
                    flag("CODIGO-012", ORIGEN_CODIGO,
                         "Overflow en exp() del segmento final.", logging.WARNING)

        return {"upper": upper, "lower": lower, "slope": pendientes, "seg": seg}

    # -----------------------------------------------------------------
    # [9.5] EVALUAR - orquestador: valida datos y junta todas las series
    # -----------------------------------------------------------------
    def evaluar(self, df: pd.DataFrame, symbol: str = "?") -> Optional[dict]:
        """
        [9.5] Pipeline completo por simbolo:
          0) validacion de datos (longitud, NaN, precios)  -> flags DATOS
          1) EMAs 9/14/50/100
          2) VWAP anclado
          3) Senales: long  = cruza EMA9>EMA100 AND EMA14>EMA50 AND close>VWAP
                      short = cruza EMA9<EMA100 AND EMA14<EMA50 AND close<VWAP
          4) estructura de pivotes -> inicio del canal
          5) canal de regresion logaritmica
        Retorna dict con arrays o None si los datos no sirven.
        """
        try:
            # Minimo dinamico: warm-up de la EMA mas larga (ema_cross) + margen
            # para estructura (antes 100+20=120; con ema_cross=200 -> 220).
            min_bars = max(120, self.cfg["ema_cross"] + 20)
            if df is None or len(df) < min_bars:
                flag("DATOS-002", ORIGEN_DATOS,
                     f"{symbol}: solo {0 if df is None else len(df)} velas "
                     f"(minimo {min_bars} para EMA{self.cfg['ema_cross']} + "
                     f"estructura).", logging.DEBUG)
                return None

            # --- Validacion de numericidad de columnas criticas ---
            for col in ("open", "high", "low", "close"):
                vals = df[col].values
                if not np.all(np.isfinite(vals)):
                    flag("DATOS-004", ORIGEN_DATOS,
                         f"{symbol}: NaN/Inf en columna '{col}'. Simbolo omitido.",
                         logging.WARNING)
                    return None
                if np.any(vals <= 0):
                    flag("DATOS-005", ORIGEN_DATOS,
                         f"{symbol}: precio <= 0 en columna '{col}'. Omiso.",
                         logging.WARNING)
                    return None

            close = df["close"]
            emas = self._emas(close)
            vwap = self._vwap(df)

            # --- Cruces EMA9 vs EMA100 (crossover/crossunder de Pine) ---
            f, x = emas["fast"].values, emas["cross"].values
            mid, slow = emas["mid"].values, emas["slow"].values
            n = len(df)
            cruza_up = np.zeros(n, dtype=bool)
            cruza_down = np.zeros(n, dtype=bool)
            valid = np.isfinite(f) & np.isfinite(x)
            for i in range(1, n):
                if valid[i] and valid[i - 1]:
                    if f[i - 1] <= x[i - 1] and f[i] > x[i]:
                        cruza_up[i] = True     # ta.crossover
                    if f[i - 1] >= x[i - 1] and f[i] < x[i]:
                        cruza_down[i] = True   # ta.crossunder

            c = close.values
            with np.errstate(invalid="ignore"):
                long_mask = (cruza_up & (mid > slow) & (c > vwap))
                short_mask = (cruza_down & (mid < slow) & (c < vwap))
            long_mask = np.nan_to_num(long_mask.astype(np.float64),
                                      nan=0.0).astype(bool)
            short_mask = np.nan_to_num(short_mask.astype(np.float64),
                                       nan=0.0).astype(bool)

            # --- Estructura + canal ---
            estructura = self._estructura(df)
            canal = self._canal(df, estructura["canal_start"])

            return {
                "symbol": symbol,
                "n": n,
                "emas": {k: v.values for k, v in emas.items()},
                "vwap": vwap,
                "long_mask": long_mask,
                "short_mask": short_mask,
                "long_idx": np.flatnonzero(long_mask).tolist(),
                "short_idx": np.flatnonzero(short_mask).tolist(),
                "estructura": estructura,
                "canal": canal,
            }
        except Exception as e:
            flag("CODIGO-006", origen_de_excepcion(e),
                 f"evaluar_signalvwap({symbol}): {type(e).__name__}: {e}",
                 logging.ERROR)
            return None


# =============================================================================
# [10] VISUALIZACION
#      [10A] GRAFICO ACTIVO estilo TradingView (SignalVWAP: velas + canal
#            relleno + triángulos LONG/SHORT + VWAP) -> se envia a Telegram
#      [10B] GRAFICO PRESERVADO de botbb_engine (2 paneles: precio + RSI)
#            conservado integro SIN su logica de trading
# =============================================================================
class GraficoSignalVWAP:
    """
    [10A.0] Genera el PNG con el aspecto de las capturas de TradingView:
      - Fondo casi negro (#131722 de TV), ejes grises tenues.
      - Velas alcistas verde menta (#26A69A), bajistas rojo (#FF4444).
      - Canal de regresion: 2 rectas + relleno translucido del color de la
        tendencia (verde alcista / rojo bajista) - igual que el fill del Pine.
      - VWAP en azul (#2962FF).
      - Senales: triangulo + texto LONG (verde, bajo la vela) /
        SHORT (rojo, sobre la vela).
      - Marcas de orden (si side): ENTRY (linea blanca + etiqueta azul y
        texto sobre la vela de senal), SL (linea roja punteada + etiqueta)
        y TP (linea verde punteada + etiqueta) en el eje derecho; el eje Y
        se amplia para que las 3 lineas siempre sean visibles.
      - Etiqueta de precio actual a la derecha (recta punteada + caja).
      - Titulo superior: simbolo, TF, OHLC y variacion (como en TV).
    """

    # Paleta de colores de TradingView (dark theme)
    COL_BG = "#131722"        # fondo del grafico
    COL_GRID = "#1e222d"      # rejilla
    COL_AXIS = "#787b86"      # texto de ejes
    COL_UP = "#26A69A"        # vela alcista / canal alcista
    COL_DOWN = "#EF5350"      # vela bajista / canal bajista
    COL_VWAP = "#2962FF"      # linea VWAP
    COL_TEXT = "#d1d4dc"      # texto claro
    COL_PRICE_LINE = "#787b86"  # linea de precio actual

    @classmethod
    def generar(cls, symbol: str, df: pd.DataFrame, res: dict,
                cfg: dict, side: str = None, idx: int = None,
                vwap: bool = True) -> Optional[BytesIO]:
        """
        [10A.1] Construye el PNG en un BytesIO (sin escribir a disco).
          symbol : par ej. BTC/USDT:USDT
          df     : OHLCV (timestamp, open, high, low, close, volume)
          res    : salida de SignalVWAP.evaluar() con series ya calculadas
          side   : 'long'|'short' (opcional: marca la senal de la ultima vela)
          idx    : indice de la vela a marcar (por defecto: ultima)
          vwap   : dibujar la linea VWAP
        Retorna BytesIO PNG o None si hay error (nunca lanza).
        """
        try:
            n = len(df)
            if n < 20 or res is None:
                flag("CHART-DATA", ORIGEN_DATOS,
                     f"{symbol}: datos insuficientes para graficar.", logging.WARNING)
                return None

            # --- Ventana visible: ultimas chart_candles velas ---
            vent = min(cfg.get("chart_candles", 120), n)
            s = n - vent
            x = np.arange(vent, dtype=np.float64)

            o = df["open"].values[s:n]
            h = df["high"].values[s:n]
            l = df["low"].values[s:n]
            c = df["close"].values[s:n]

            fig, ax = plt.subplots(figsize=(12, 6), facecolor=cls.COL_BG)
            ax.set_facecolor(cls.COL_BG)

            # --- Rejilla y ejes (estilo TV: eje de precios a la DERECHA) ---
            ax.grid(True, color=cls.COL_GRID, linewidth=0.6, alpha=0.8)
            ax.set_axisbelow(True)
            for sp in ax.spines.values():
                sp.set_color(cls.COL_GRID)
            ax.tick_params(colors=cls.COL_AXIS, labelsize=8)

            # --- Velas japonesas ---
            for i in range(vent):
                alcista = c[i] >= o[i]
                col = cls.COL_UP if alcista else cls.COL_DOWN
                # Mecha (high-low)
                ax.plot([x[i], x[i]], [l[i], h[i]], color=col,
                        linewidth=0.9, solid_capstyle="round", zorder=3)
                # Cuerpo (open-close) - minimo visible para dojis
                cuerpo = abs(c[i] - o[i])
                rango = h[i] - l[i]
                if cuerpo < rango * 0.001:
                    cuerpo = max(rango * 0.005, 1e-12)
                bajo = min(o[i], c[i])
                ax.add_patch(plt.Rectangle(
                    (x[i] - 0.35, bajo), 0.7, cuerpo,
                    facecolor=col, edgecolor=col, linewidth=0.4, zorder=4))

            # --- CANAL de Regresion Logaritmica (rectas + relleno) ---
            seg = res.get("canal", {}).get("seg", {})
            if seg and seg.get("ok"):
                # Coordenadas relativas a la ventana visible
                x0 = seg["x0"] - s
                x1 = seg["x1"] - s
                # Recortar lo que quede fuera a la izquierda
                if x0 < -1:
                    # interpolar el valor del canal en x=-1
                    fr = (-1 - x0) / max(x1 - x0, 1e-9)
                    yu0 = seg["y_up0"] + (seg["y_up1"] - seg["y_up0"]) * fr
                    yl0 = seg["y_lo0"] + (seg["y_lo1"] - seg["y_lo0"]) * fr
                    x0 = -1.0
                else:
                    yu0, yl0 = seg["y_up0"], seg["y_lo0"]

                col_canal = (cls.COL_UP if seg["trend"] > 0 else
                             cls.COL_DOWN if seg["trend"] < 0 else cls.COL_AXIS)
                # Rectas superior e inferior
                ax.plot([x0, x1], [yu0, seg["y_up1"]], color=col_canal,
                        linewidth=cfg.get("channel_line_width", 1.0), zorder=2)
                ax.plot([x0, x1], [yl0, seg["y_lo1"]], color=col_canal,
                        linewidth=cfg.get("channel_line_width", 1.0), zorder=2)
                # Relleno translucido (linefill.new del Pine):
                # color.new(green/red, 90) -> alfa 10%; color.new(gray, 95) -> 5%
                ax.fill_between([x0, x1], [yu0, seg["y_up1"]],
                                [yl0, seg["y_lo1"]],
                                color=col_canal,
                                alpha=0.10 if seg["trend"] != 0 else 0.05,
                                zorder=1)

            # --- VWAP ---
            if vwap:
                vw = res.get("vwap")[s:n] if res.get("vwap") is not None else None
                if vw is not None:
                    okv = np.isfinite(vw)
                    ax.plot(x[okv], vw[okv], color=cls.COL_VWAP,
                            linewidth=1.3, zorder=5, label="VWAP")

            # --- Senales LONG/SHORT (triangulos + texto, como plotshape) ---
            # Nota: los arrays o/h/l/c ya estan recortados a la ventana;
            # se usa el indice LOCAL (ii = i - s) para indexarlos.
            li = [i for i in res.get("long_idx", []) if s <= i < n]
            si = [i for i in res.get("short_idx", []) if s <= i < n]
            # Cerca del borde derecho el texto centrado se sale del lienzo:
            # se desplaza a la izquierda del triangulo (ha="right").
            for i in li:
                ii = i - s
                yb = l[ii] - (h[ii] - l[ii]) * 0.35 - (c[ii] * 0.0015)
                ax.plot(ii, yb, marker="^", color=cls.COL_UP, markersize=8,
                        zorder=6)
                cerca = ii > vent * 0.85
                ax.annotate("LONG", xy=(ii, yb),
                            xytext=(-7 if cerca else 0, -14),
                            textcoords="offset points",
                            ha="right" if cerca else "center",
                            color=cls.COL_UP, fontsize=9, fontweight="bold",
                            zorder=6)
            for i in si:
                ii = i - s
                yt = h[ii] + (h[ii] - l[ii]) * 0.35 + (c[ii] * 0.0015)
                ax.plot(ii, yt, marker="v", color=cls.COL_DOWN, markersize=8,
                        zorder=6)
                cerca = ii > vent * 0.85
                ax.annotate("SHORT", xy=(ii, yt),
                            xytext=(-7 if cerca else 0, 10),
                            textcoords="offset points",
                            ha="right" if cerca else "center",
                            color=cls.COL_DOWN, fontsize=9, fontweight="bold",
                            zorder=6)

            # --- SL/TP de la orden [11.4] para las marcas de orden ---
            # (mismos valores que usaria open_position; solo matematica,
            #  sin llamadas a la API: el grafico se marca aunque
            #  trading_habilitado=False). [TPO-2] ahora son SL + TP1 + TP2.
            sl = tp1 = tp2 = entry = None
            if side:
                k = idx if (idx is not None and 0 <= idx < n) else (n - 1)
                try:
                    canal_up = res.get("canal", {}).get("upper")
                    canal_lo = res.get("canal", {}).get("lower")
                    cu = float(canal_up[k]) if canal_up is not None else np.nan
                    cl = float(canal_lo[k]) if canal_lo is not None else np.nan
                    entry = float(df["close"].iloc[k])
                    sl, tp1, tp2 = _sl_tp_desde_canal(side, entry, cu, cl, cfg,
                                                      res=res, df=df)
                    if not (np.isfinite(sl) and np.isfinite(tp1)
                            and np.isfinite(tp2)):
                        sl = tp1 = tp2 = None
                except Exception as e:
                    flag("CHART-SLTP", origen_de_excepcion(e),
                         f"{symbol}: marcas SL/TP no disponibles: "
                         f"{type(e).__name__}: {e}", logging.ERROR)
                    sl = tp1 = tp2 = None
            marcado = sl is not None and tp1 is not None and tp2 is not None

            # --- Marca de la senal ACTUAL (si dispara en esta vela) ---
            if side and idx is not None and s <= idx < n:
                xi = idx - s
                ax.axvline(x=xi, color=cls.COL_PRICE_LINE, linestyle=":",
                           linewidth=1.0, alpha=0.6, zorder=2)
                if marcado:
                    # Texto ENTRY junto a la vela de senal (libre de las
                    # etiquetas LONG/SHORT: long->arriba, short->abajo)
                    rango_v = max(float(h[xi]) - float(l[xi]), 1e-12)
                    if side == "long":
                        ye, va = float(h[xi]) + rango_v * 0.55, "bottom"
                    else:
                        ye, va = float(l[xi]) - rango_v * 0.55, "top"
                    cerca = xi > vent * 0.85
                    ax.annotate("ENTRY", xy=(xi, ye),
                                xytext=(-7 if cerca else 0, 0),
                                textcoords="offset points",
                                ha="right" if cerca else "center", va=va,
                                color="#FFFFFF", fontsize=9,
                                fontweight="bold", zorder=7)

            # --- Linea de precio actual + etiqueta (caja roja/verde de TV);
            #     con side pasa a ser la marca ENTRY (etiqueta azul) + las
            #     lineas punteadas SL (rojo) y TP (verde) del eje derecho ---
            ultimo = float(c[-1])
            col_ult = cls.COL_UP if (len(c) > 1 and c[-1] >= c[-2]) else cls.COL_DOWN
            if marcado:
                ax.axhline(y=entry, color="#FFFFFF", linestyle="--",
                           linewidth=1.0, alpha=0.75, zorder=3)
                ax.annotate(f"ENTRY {entry:.6g}", xy=(1.002, entry),
                            xycoords=("axes fraction", "data"),
                            fontsize=8, color="white", va="center",
                            bbox=dict(boxstyle="round,pad=0.25",
                                      fc="#2962FF", ec="none"),
                            annotation_clip=False, zorder=8)
                ax.axhline(y=sl, color=cls.COL_DOWN, linestyle="--",
                           linewidth=1.1, alpha=0.9, zorder=3)
                ax.annotate(f"SL {sl:.6g}", xy=(1.002, sl),
                            xycoords=("axes fraction", "data"),
                            fontsize=8, color="white", va="center",
                            bbox=dict(boxstyle="round,pad=0.25",
                                      fc=cls.COL_DOWN, ec="none"),
                            annotation_clip=False, zorder=8)
                ax.axhline(y=tp1, color=cls.COL_UP, linestyle="--",
                           linewidth=1.1, alpha=0.9, zorder=3)
                ax.annotate(f"TP1 {tp1:.6g}", xy=(1.002, tp1),
                            xycoords=("axes fraction", "data"),
                            fontsize=8, color="white", va="center",
                            bbox=dict(boxstyle="round,pad=0.25",
                                      fc=cls.COL_UP, ec="none"),
                            annotation_clip=False, zorder=8)
                ax.axhline(y=tp2, color=cls.COL_UP, linestyle=":",
                           linewidth=1.3, alpha=0.95, zorder=3)
                ax.annotate(f"TP2 {tp2:.6g}", xy=(1.002, tp2),
                            xycoords=("axes fraction", "data"),
                            fontsize=8, color="white", va="center",
                            bbox=dict(boxstyle="round,pad=0.25",
                                      fc=cls.COL_UP, ec="none"),
                            annotation_clip=False, zorder=8)
            else:
                ax.axhline(y=ultimo, color=cls.COL_PRICE_LINE, linestyle="--",
                           linewidth=0.8, alpha=0.7, zorder=2)
                ax.annotate(f"{ultimo:.6g}", xy=(1.002, ultimo),
                            xycoords=("axes fraction", "data"),
                            fontsize=8, color="white", va="center",
                            bbox=dict(boxstyle="round,pad=0.25", fc=col_ult,
                                      ec="none"),
                            annotation_clip=False, zorder=7)

            # --- Titulo superior estilo TradingView (3 lineas, sin solape) ---
            # L1 simbolo+TF | L2 OHLC+variacion | L3 nombre del indicador.
            # Se dibujan DENTRO del eje (transAxes) como hace TradingView.
            var = ultimo - float(c[0])
            var_pct = (var / float(c[0]) * 100) if float(c[0]) != 0 else 0.0
            base = symbol.split(":")[0].replace("/", " / ")
            tf = cfg.get("timeframe", "?")
            ax.text(0.012, 0.985, f"{base}  ·  {tf}",
                    transform=ax.transAxes, color=cls.COL_TEXT,
                    fontsize=10, fontweight="bold", va="top", zorder=8)
            ax.text(0.012, 0.930,
                    (f"O{c[0]:.6g} H{h.max():.6g} L{l.min():.6g} "
                     f"C{ultimo:.6g} {var:+.6g} ({var_pct:+.2f}%)"),
                    transform=ax.transAxes, color=cls.COL_TEXT,
                    fontsize=8, va="top", zorder=8)
            # Leyenda de la estrategia (como la linea del indicador en TV)
            ax.text(0.012, 0.875, "SignalVWAP Clean",
                    transform=ax.transAxes, color=cls.COL_AXIS,
                    fontsize=8, va="top", zorder=8)

            # --- Eje X: horas visibles ---
            step = max(1, vent // 7)
            ticks = list(range(0, vent, step))
            fechas = pd.to_datetime(df["timestamp"].values[s:n], unit="ms", utc=True)
            etiquetas = [fechas[i].strftime("%H:%M") for i in ticks]
            ax.set_xticks(ticks)
            ax.set_xticklabels(etiquetas)
            ax.set_xlim(-1, vent)

            # --- Eje Y a la derecha (como TradingView) ---
            ax.yaxis.tick_right()
            ax.yaxis.set_label_position("right")
            ax.tick_params(axis="y", pad=4)

            # --- Eje Y: ampliar la vista para que ENTRY/SL/TP sean
            #     siempre visibles (lineas dentro del grafico) ---
            ymin = float(np.nanmin(l))
            ymax = float(np.nanmax(h))
            if marcado:
                ymin = min(ymin, float(sl), float(tp1), float(tp2))
                ymax = max(ymax, float(sl), float(tp1), float(tp2))
            pad_y = (ymax - ymin) * 0.07
            if pad_y > 0:
                ax.set_ylim(ymin - pad_y, ymax + pad_y)

            fig.tight_layout()
            buf = BytesIO()
            fig.savefig(buf, format="png", dpi=cfg.get("chart_dpi", 100),
                        facecolor=cls.COL_BG, bbox_inches="tight")
            buf.seek(0)
            plt.close(fig)
            log.info(f"[CHART] Grafico SignalVWAP generado para {symbol}")
            return buf

        except Exception as e:
            flag("CHART-UNEXPECTED", origen_de_excepcion(e),
                 f"Generando grafico {symbol}: {type(e).__name__}: {e}",
                 logging.ERROR)
            try:
                plt.close("all")  # liberar figuras huérfanas (memoria)
            except Exception:
                pass
            return None


# -----------------------------------------------------------------------------
# [10B] GRAFICO PRESERVADO de botbb_engine.py - 2 paneles (precio + RSI)
#       Conservado INTEGRO (requisito 3): logica de visualizacion original.
#       NO se invoca en el flujo activo de canalBot (su logica de trading
#       fue excluida - flag CODIGO-003). Se mantiene para futuras aplicaciones
#       y para poder replicar la imagen original cuando se requiera.
# -----------------------------------------------------------------------------
def generar_grafico_signal_botbb(
    symbol: str,
    df: pd.DataFrame,
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    entry_idx: int = None,
    v0_idx: int = None,
    confirm_idx: int = None,
    div_info: dict = None,
) -> Optional[BytesIO]:
    """
    [10B.1] Grafico PNG dark-theme de botbb_engine (PRESERVADO):
      Panel 1 (3/4): velas Heikin Ashi + Bollinger + Signal Line + VWAP
                     + marcas ENTRY/V0/CONF + lineas SL/TP/Entry
                     + lineas de divergencia amarillas.
      Panel 2 (1/4): RSI (purpura) + StochRSI %K/%D + zonas 70/30.
      Mismos calculos matematicos que el bot original (seccion [7]).
    """
    try:
        if df is None or len(df) < 30:
            flag("CHART10B-DATA", ORIGEN_DATOS,
                 f"{symbol}: datos insuficientes para grafico 10B.", logging.WARNING)
            return None

        # --- Series matematicas preservadas (seccion [7]) ---
        bb_upper_full, bb_basis_full, bb_lower_full = _math.calculate_bb(
            df["close"], MATH_CFG)
        signal_line_full = _math.calculate_signal_line(df["close"], MATH_CFG)
        vwap_full = _math.calculate_vwap(df)
        ha_df = _math.heikin_ashi(df)
        rsi_full = _math.calculate_rsi(df["close"], MATH_CFG)
        k_full, d_full = _math.calculate_stochrsi(df["close"], MATH_CFG)

        center = entry_idx if entry_idx is not None else len(df) // 2
        before, after = 40, 15

        # Si hay divergencia, expandir ventana hacia atras para incluir pivotes
        if (div_info and div_info.get("p1_idx") is not None
                and div_info.get("div_start") is not None):
            earliest_pivot = min(div_info["p1_idx"], div_info["p2_idx"]) + \
                div_info["div_start"]
            needed_before = center - earliest_pivot + 5
            before = max(before, needed_before)

        s = max(0, center - before)
        e = min(len(df), center + after)

        # Recortes de la ventana
        o_w = ha_df["ha_open"].values[s:e]
        h_w = ha_df["ha_high"].values[s:e]
        l_w = ha_df["ha_low"].values[s:e]
        c_w = ha_df["ha_close"].values[s:e]
        bb_u = bb_upper_full.values[s:e]
        signal_line_w = signal_line_full.values[s:e]
        bb_l = bb_lower_full.values[s:e]
        vwap_w = vwap_full.values[s:e]
        rsi_w = rsi_full.values[s:e]
        k_w = k_full.values[s:e]
        d_w = d_full.values[s:e]
        n_w = e - s
        x = np.arange(n_w)

        local_entry = (entry_idx - s) if entry_idx is not None else None
        local_v0 = (v0_idx - s) if v0_idx is not None else None
        local_confirm = (confirm_idx - s) if confirm_idx is not None else None

        # === FIGURA CON 2 PANELES ===
        fig, (ax, ax2) = plt.subplots(
            2, 1, figsize=(14, 9), facecolor='#1a1a1a',
            gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
        fig.subplots_adjust(hspace=0.05)

        # --- PANEL 1: PRECIO ---
        ax.set_facecolor('#1a1a1a')
        ax.tick_params(colors='white', labelsize=8)
        ax.grid(True, color='#333333', linewidth=0.3, alpha=0.5)
        for spine in ax.spines.values():
            spine.set_color('#333333')

        # Velas Heikin Ashi (verde/rojo)
        for i in range(n_w):
            color = '#26A69A' if c_w[i] >= o_w[i] else '#FF4444'
            ax.plot([x[i], x[i]], [l_w[i], h_w[i]], color=color, linewidth=0.8)
            body_bottom = min(o_w[i], c_w[i])
            body_height = abs(c_w[i] - o_w[i])
            if body_height < (h_w[i] - l_w[i]) * 0.001:
                body_height = (h_w[i] - l_w[i]) * 0.003
            rect = plt.Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height,
                                 facecolor=color, edgecolor=color, linewidth=0.5)
            ax.add_patch(rect)

        # Bollinger Bands
        valid_u = ~np.isnan(bb_u)
        valid_l = ~np.isnan(bb_l)
        ax.plot(x[valid_u], bb_u[valid_u], color='#FF0000', linewidth=1.0,
                label='BB Upper')
        ax.plot(x[valid_l], bb_l[valid_l], color='#26A69A', linewidth=1.0,
                label='BB Lower')

        # Signal Line coloreada por estado del MACD (verde/rojo)
        macd_fast = MathPreservados.ema_tv(df["close"], MATH_CFG["macd_fast"])
        macd_slow = MathPreservados.ema_tv(df["close"], MATH_CFG["macd_slow"])
        macd_val = macd_fast - macd_slow
        signal_val = macd_val.rolling(MATH_CFG["macd_signal"]).mean()
        macd_green_full = (macd_val >= signal_val)
        macd_green_w = macd_green_full.values[s:e]
        for i in range(1, n_w):
            if np.isnan(signal_line_w[i]) or np.isnan(signal_line_w[i - 1]):
                continue
            color = '#26A69A' if macd_green_w[i] else '#FF4444'
            ax.plot([x[i - 1], x[i]],
                    [signal_line_w[i - 1], signal_line_w[i]],
                    color=color, linewidth=1.5,
                    label='Signal Line' if i == 1 else '')

        # VWAP
        valid_vwap = ~np.isnan(vwap_w)
        ax.plot(x[valid_vwap], vwap_w[valid_vwap], color='#2962FF',
                linewidth=1.5, label='VWAP')

        # Niveles Entry / SL / TP (lineas horizontales punteadas)
        ax.axhline(y=entry_price, color='#FFD700', linestyle='--',
                   linewidth=1.5, alpha=0.8, label=f'Entry {entry_price:.4f}')
        ax.axhline(y=sl_price, color='#FF0000', linestyle='--',
                   linewidth=1.5, alpha=0.8, label=f'SL {sl_price:.4f}')
        ax.axhline(y=tp_price, color='#00FF00', linestyle='--',
                   linewidth=1.5, alpha=0.8, label=f'TP {tp_price:.4f}')

        # Marcador ENTRY (flecha + texto)
        if local_entry is not None and 0 <= local_entry < n_w:
            rng = (h_w[local_entry] - l_w[local_entry]
                   if h_w[local_entry] != l_w[local_entry]
                   else entry_price * 0.002)
            offset = rng * 0.5
            marker_y = entry_price - offset if side == "long" else entry_price + offset
            marker_color = '#00FF00' if side == 'long' else '#FF4444'
            marker_symbol = '^' if side == 'long' else 'v'
            ax.plot(x[local_entry], marker_y, marker=marker_symbol,
                    color=marker_color, markersize=14, zorder=5)
            ax.axvline(x=x[local_entry], color=marker_color, linestyle=':',
                       linewidth=1.2, alpha=0.7)
            ax.annotate('ENTRY', xy=(x[local_entry], entry_price),
                        xytext=(x[local_entry] + 2, entry_price),
                        fontsize=10, color=marker_color, fontweight='bold',
                        arrowprops=dict(arrowstyle='->', color=marker_color, lw=1.5))

        # Marcador V0 (vela de origen de la senal)
        if local_v0 is not None and 0 <= local_v0 < n_w:
            v0_color = '#FF6600'
            candle_range = h_w[local_v0] - l_w[local_v0]
            if candle_range < entry_price * 0.001:
                candle_range = entry_price * 0.003
            if side == "long":
                v0_y = l_w[local_v0] - candle_range * 0.3
                v0_symbol, v0_va = 'v', 'top'
            else:
                v0_y = h_w[local_v0] + candle_range * 0.3
                v0_symbol, v0_va = '^', 'bottom'
            ax.plot(x[local_v0], v0_y, marker=v0_symbol, color=v0_color,
                    markersize=10, zorder=5)
            ax.annotate('V0', xy=(x[local_v0], v0_y),
                        xytext=(x[local_v0],
                                v0_y + (candle_range * 0.4 if side == "long"
                                        else -candle_range * 0.4)),
                        fontsize=8, color=v0_color, fontweight='bold',
                        ha='center', va=v0_va)

        # Marcador CONF (vela de confirmacion)
        if local_confirm is not None and 0 <= local_confirm < n_w:
            conf_color = '#00BFFF'
            candle_range_c = h_w[local_confirm] - l_w[local_confirm]
            if candle_range_c < entry_price * 0.001:
                candle_range_c = entry_price * 0.003
            if side == "long":
                conf_y = l_w[local_confirm] - candle_range_c * 0.3
                conf_symbol, conf_va = 'v', 'top'
            else:
                conf_y = h_w[local_confirm] + candle_range_c * 0.3
                conf_symbol, conf_va = '^', 'bottom'
            ax.plot(x[local_confirm], conf_y, marker=conf_symbol,
                    color=conf_color, markersize=10, zorder=5)
            ax.annotate('CONF', xy=(x[local_confirm], conf_y),
                        xytext=(x[local_confirm],
                                conf_y + (candle_range_c * 0.4 if side == "long"
                                         else -candle_range_c * 0.4)),
                        fontsize=8, color=conf_color, fontweight='bold',
                        ha='center', va=conf_va)

        # Titulo con info de la senal (+ divergencia si existe)
        side_label = "LONG" if side == "long" else "SHORT"
        safe_symbol = ''.join(ch for ch in symbol if ord(ch) < 128)
        vwap_current = (vwap_w[-1] if len(vwap_w) > 0
                        and not np.isnan(vwap_w[-1]) else 0)
        div_label = ""
        if div_info:
            div_type = div_info.get("rsi_div") or div_info.get("stochrsi_div") or "?"
            div_src = "RSI" if div_info.get("rsi_div") else "StochRSI"
            div_label = f" | Div: {div_src} {div_type.upper()}"
        titulo = (f"{safe_symbol} | {side_label} | "
                  f"Entry: {entry_price:.6f} | SL: {sl_price:.6f} | "
                  f"TP: {tp_price:.6f} | VWAP: {vwap_current:.6f}{div_label}")
        ax.set_title(titulo, color='white', fontsize=10, fontweight='bold', pad=10)
        ax.set_ylabel('Precio (USDT)', color='white', fontsize=9)
        ax.legend(loc='upper left', fontsize=7, facecolor='#1a1a1a',
                  edgecolor='#444', labelcolor='white')
        ax.set_xlim(-1, n_w)

        # --- PANEL 2: RSI + STOCHRSI ---
        ax2.set_facecolor('#1a1a1a')
        ax2.tick_params(colors='white', labelsize=8)
        ax2.grid(True, color='#333333', linewidth=0.3, alpha=0.5)
        for spine in ax2.spines.values():
            spine.set_color('#333333')

        # Bandas de referencia 70/30/50
        ax2.axhline(y=70, color='#787B86', linewidth=0.8, linestyle='--')
        ax2.axhline(y=50, color='#787B86', linewidth=0.5, linestyle=':', alpha=0.5)
        ax2.axhline(y=30, color='#787B86', linewidth=0.8, linestyle='--')
        ax2.axhline(y=100, color='#787B86', linewidth=0.5, alpha=0.3)
        ax2.axhline(y=0, color='#787B86', linewidth=0.5, alpha=0.3)

        # RSI (purpura, identico a TradingView)
        valid_rsi = ~np.isnan(rsi_w)
        ax2.plot(x[valid_rsi], rsi_w[valid_rsi], color='#7E57C2',
                 linewidth=1.5, label='RSI', zorder=3)

        # Zonas degradadas sobreventa/sobrecompra
        ax2.fill_between(x, 70, 100, where=(rsi_w > 70) & ~np.isnan(rsi_w),
                         color='#26A69A', alpha=0.15, interpolate=True)
        ax2.fill_between(x, 0, 30, where=(rsi_w < 30) & ~np.isnan(rsi_w),
                         color='#FF4444', alpha=0.15, interpolate=True)

        # %K azul / %D naranja
        valid_k = ~np.isnan(k_w)
        ax2.plot(x[valid_k], k_w[valid_k], color='#2962FF', linewidth=1.5,
                 label='%K', zorder=4)
        valid_d = ~np.isnan(d_w)
        ax2.plot(x[valid_d], d_w[valid_d], color='#FF6D00', linewidth=1.5,
                 label='%D', zorder=4)

        ax2.set_ylabel('RSI + Stoch', color='white', fontsize=9)
        ax2.set_ylim(-5, 105)
        ax2.legend(loc='upper left', fontsize=7, facecolor='#1a1a1a',
                   edgecolor='#444', labelcolor='white')

        # Linea vertical punteada de entrada (conecta ambos paneles)
        if local_entry is not None and 0 <= local_entry < n_w:
            ax.axvline(x=x[local_entry], color='#FFD700', linestyle=':',
                       linewidth=0.8, alpha=0.4)
            ax2.axvline(x=x[local_entry], color='#FFD700', linestyle=':',
                        linewidth=0.8, alpha=0.4)

        # --- LINEAS DE DIVERGENCIA (amarillas, panel precio + panel RSI) ---
        if div_info and local_v0 is not None and 0 <= local_v0 < n_w:
            div_type = div_info.get("rsi_div") or div_info.get("stochrsi_div")
            div_start = div_info.get("div_start", 0)
            p1_idx_raw = div_info.get("p1_idx")
            p1_price = div_info.get("p1_price")
            p1_indic = div_info.get("p1_indic")
            p2_idx_raw = div_info.get("p2_idx")
            p2_price = div_info.get("p2_price")
            p2_indic = div_info.get("p2_indic")

            if (div_type and p1_idx_raw is not None and p2_idx_raw is not None
                    and p1_price is not None and p2_price is not None):
                g1 = p1_idx_raw + div_start - s
                g2 = p2_idx_raw + div_start - s
                min_div_distance = 6  # pivotes minimos a 6 velas
                if abs(g2 - g1) < min_div_distance:
                    log.debug(f"[CHART10B] Div descartada {symbol}: "
                              f"pivotes a {abs(g2-g1)} velas (<{min_div_distance})")
                elif 0 <= g1 < n_w and 0 <= g2 < n_w:
                    ax.plot([g1, g2], [p1_price, p2_price],
                            color='#FFD700', linewidth=2.0, linestyle='-', zorder=6)
                    ax2.plot([g1, g2], [p1_indic, p2_indic],
                             color='#FFD700', linewidth=2.0, linestyle='-', zorder=6)
                    mid_x = (g1 + g2) / 2
                    if div_type == "bull":
                        mid_y = min(p1_price, p2_price) * 0.998
                        ax.annotate('BULL DIV', xy=(mid_x, mid_y), fontsize=8,
                                    color='#FFD700', fontweight='bold',
                                    ha='center', va='top')
                        ax2.annotate('BULL DIV',
                                     xy=(mid_x, min(p1_indic, p2_indic) - 3),
                                     fontsize=8, color='#FFD700',
                                     fontweight='bold', ha='center', va='top')
                    else:
                        mid_y = max(p1_price, p2_price) * 1.002
                        ax.annotate('BEAR DIV', xy=(mid_x, mid_y), fontsize=8,
                                    color='#FFD700', fontweight='bold',
                                    ha='center', va='bottom')
                        ax2.annotate('BEAR DIV',
                                     xy=(mid_x, max(p1_indic, p2_indic) + 3),
                                     fontsize=8, color='#FFD700',
                                     fontweight='bold', ha='center', va='bottom')

        # Eje X solo en el panel inferior (etiquetas +Nc)
        step = max(1, n_w // 8)
        ticks = list(range(0, n_w, step))
        labels = [f'+{i}c' for i in ticks]
        ax2.set_xticks(ticks)
        ax2.set_xticklabels(labels, color='white', fontsize=8)
        ax.set_xticklabels([])

        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                    facecolor="#1a1a1a")
        buf.seek(0)
        plt.close(fig)
        log.info(f"[CHART10B] Grafico botbb generado para {symbol}")
        return buf

    except Exception as e:
        flag("CHART10B-UNEXPECTED", origen_de_excepcion(e),
             f"Grafico 10B {symbol}: {type(e).__name__}: {e}", logging.ERROR)
        try:
            plt.close("all")
        except Exception:
            pass
        return None


# =============================================================================
# [11] LOOP PRINCIPAL ASYNC - escaneo TOP100 + senales + Telegram + trading
# =============================================================================
def timeframe_a_ms(tf: str) -> int:
    """[11.1] Convierte timeframe ('5m','1h','15m') a milisegundos."""
    unidades = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    try:
        num = int(tf[:-1])
        return num * unidades.get(tf[-1], 60_000)
    except (ValueError, IndexError):
        flag("TF-PARSE", ORIGEN_CODIGO,
             f"timeframe no reconocido: '{tf}'. Uso 5m por defecto.", logging.WARNING)
        return 300_000


async def loop_principal(bot: CanalBot, strat: SignalVWAP):
    """
    [11.2] Bucle infinito resiliente (objetivo uptime 99.9%):
      - 1er ciclo: conectar + sincronizar posiciones abiertas.
      - Cada 15s: gestionar posiciones vivas (BE/trailing/cierres) [solo si
        trading_habilitado].
      - Cada scan_interval_sec: descargar TOP100 OHLCV, evaluar SignalVWAP
        en la ultima vela, generar grafico estilo TV y enviarlo a Telegram.
      - NINGUN excepcion materializa fuera del try: cada tipo tiene su flag
        (429 / NET / AUTH / 500 / inesperada) y el bucle sigue vivo.
    """
    if not await bot.start():
        flag("BOOT-CONN", ORIGEN_CODIGO,
             "No se pudo conectar a Bitget. Revisa las API keys del entorno.",
             logging.CRITICAL)
        return

    # Sincronizar posiciones abiertas tras un reinicio (persistencia JSON)
    try:
        open_pos = await bot.get_open_symbols()
        bot.session_active = open_pos
        if open_pos:
            log.info(f"Posiciones abiertas detectadas al arrancar: {open_pos}")
        else:
            log.info("Sin posiciones abiertas al arrancar.")
    except Exception as e:
        flag("BOOT-SYNC", origen_de_excepcion(e),
             f"No se pudieron sincronizar posiciones: {e}", logging.WARNING)

    trading = bool(bot.cfg.get("trading_habilitado", False))
    # Cortafuegos: sin API keys no hay trading REAL aunque [3.6] diga True
    # (alinea el codigo con el aviso de la seccion [6]).
    if trading and not (API_KEY and SECRET_KEY and PASSPHRASE):
        trading = False
        flag("ENV-NOKEYS", ORIGEN_DATOS,
             "trading_habilitado=True pero faltan BITGET_API_KEY/SECRET_KEY/"
             "PASSPHRASE: se opera en modo SOLO ALERTAS.", logging.CRITICAL)
    if trading:
        log.warning("[MODO] TRADING REAL HABILITADO: se enviaran ordenes a Bitget.")
    else:
        log.info("[MODO] SOLO ALERTAS: no se envian ordenes "
                 "(activa trading_habilitado=True en [3.6] para operar).")

    log.info(f"canalBot arrancado | TF={bot.cfg['timeframe']} | "
             f"TOP={bot.cfg['top_symbols_count']} | "
             f"AnclaVWAP={bot.cfg['vwap_anchor']} | "
             f"MaxPos={bot.cfg['max_open_positions']} | "
             f"Semaforo={bot.cfg['max_concurrent_fetches']}")

    tf_ms = timeframe_a_ms(bot.cfg["timeframe"])
    bot.last_scan_time = 0.0

    try:
        while True:
            try:
                # ---- (A) Gestion de posiciones cada 15s ----
                balance = await bot.get_balance()
                if trading:
                    await bot.manage_positions(balance)

                # ---- (B) Escaneo periodico de senales ----
                elapsed = time.time() - bot.last_scan_time
                if elapsed >= bot.cfg["scan_interval_sec"]:
                    en_cooldown = bot.is_on_cooldown()
                    hueco = (await bot.can_open()) if trading else True
                    if not en_cooldown and hueco:
                        log.info(f"Escaneando TOP {bot.cfg['top_symbols_count']} "
                                 f"simbolos...")
                        top = await bot.get_top_symbols(bot.cfg["top_symbols_count"])
                        if top:
                            await _procesar_top(bot, strat, top, tf_ms, balance,
                                                trading)
                    elif en_cooldown:
                        log.debug("En cooldown: escaneo de senales pausado.")
                    elif not hueco:
                        log.debug("Maximo de posiciones alcanzado: sin escaneo.")
                    bot.last_scan_time = time.time()

                await asyncio.sleep(15)

            except RateLimitExceeded:
                flag("429", ORIGEN_BIBLIOTECA,
                     "Ciclo principal: rate limit de Bitget. Espero 30s.",
                     logging.WARNING)
                await asyncio.sleep(30)
            except (NetworkError, RequestTimeout) as e:
                flag("NET", ORIGEN_BIBLIOTECA,
                     f"Ciclo principal: error de red ({e}). Reconecto en 15s.",
                     logging.WARNING)
                await asyncio.sleep(15)
                try:
                    await bot._connect()
                except Exception as e2:
                    flag("RECONN", origen_de_excepcion(e2),
                         f"Reconexion fallida: {e2}", logging.WARNING)
            except AuthenticationError as e:
                flag("AUTH", ORIGEN_BIBLIOTECA,
                     f"Credenciales invalidas, deteniendo: {e}", logging.CRITICAL)
                break
            except PermissionDenied as e:
                flag("PERM", ORIGEN_BIBLIOTECA,
                     f"Sin permisos, deteniendo: {e}", logging.CRITICAL)
                break
            except ExchangeNotAvailable as e:
                flag("503", ORIGEN_BIBLIOTECA,
                     f"Bitget caido/mantenimiento: {e}. Reintento en 30s.",
                     logging.WARNING)
                await asyncio.sleep(30)
            except ExchangeError as e:
                flag("500", ORIGEN_BIBLIOTECA,
                     f"Error del exchange en ciclo: {e}. Continuo.", logging.ERROR)
                await asyncio.sleep(15)
            except Exception as e:
                flag("LOOP-UNEXPECTED", origen_de_excepcion(e),
                     f"Error no controlado en ciclo principal: "
                     f"{type(e).__name__}: {e}", logging.ERROR)
                await asyncio.sleep(15)

    except KeyboardInterrupt:
        log.info("Bot detenido por el usuario (Ctrl+C).")
    finally:
        await bot.stop()


async def _macd_color_15m(bot: CanalBot, symbol: str) -> Optional[bool]:
    """
    [11.3b] FILTRO DE ENTRADA: color de la Signal Line MACD en 15m.
      Devuelve:
        True  -> ultima vela 15m CERRADA con Signal Line VERDE (macd >= signal)
        False -> Signal Line ROJA  (macd <  signal)
        None  -> NO evaluable (sin datos, warmup insuficiente o error de red)
      [15M-ONLY] Esta es la UNICA condicion medida en 15m (CONFIG
      macd_filter_tf); el resto de la estrategia opera en CONFIG["timeframe"]
      (5m). Solo se mira el COLOR: no se diferencia pendiente alcista/bajista.
      Con macd_filter_enabled=False se salta la descarga y devuelve True.
    """
    if not bot.cfg.get("macd_filter_enabled", True):
        return True  # filtro desactivado -> condicion cumplida

    tf = bot.cfg.get("macd_filter_tf", "15m")
    try:
        data = await bot.fetch_ohlcv_batch(
            [symbol], tf, int(bot.cfg.get("macd_filter_limit", 300)))
    except RateLimitExceeded:
        flag("429", ORIGEN_BIBLIOTECA,
             f"MACD15m {symbol}: rate limit.", logging.WARNING)
        return None
    except (NetworkError, RequestTimeout):
        flag("NET", ORIGEN_BIBLIOTECA,
             f"MACD15m {symbol}: error de red.", logging.WARNING)
        return None
    except Exception as e:
        flag("MACD15M-UNEXPECTED", origen_de_excepcion(e),
             f"MACD15m {symbol}: {type(e).__name__}: {e}", logging.ERROR)
        return None

    filas = data.get(symbol)
    if not filas:
        flag("MACD15M-DATOS", ORIGEN_DATOS,
             f"{symbol}: sin velas {tf} para el filtro MACD.", logging.WARNING)
        return None

    df15 = pd.DataFrame(
        filas, columns=["timestamp", "open", "high", "low", "close", "volume"])

    # Solo la ULTIMA VELA 15m CERRADA (la vela en formacion repintaria)
    tf_ms = timeframe_a_ms(tf)
    ahora = time.time() * 1000.0
    cerradas = df15[df15["timestamp"] + tf_ms <= ahora]

    min_bars = MATH_CFG["macd_slow"] + MATH_CFG["macd_signal"] + 5  # 40
    if len(cerradas) < min_bars:
        flag("MACD15M-DATOS", ORIGEN_DATOS,
             f"{symbol}: solo {len(cerradas)} velas {tf} cerradas "
             f"(minimo {min_bars}).", logging.WARNING)
        return None

    c15 = cerradas["close"].values
    if not np.all(np.isfinite(c15)) or np.any(c15 <= 0):
        flag("MACD15M-DATOS", ORIGEN_DATOS,
             f"{symbol}: precios NaN/<=0 en velas {tf}.", logging.WARNING)
        return None

    # True = VERDE (macd >= signal) / False = ROJA. Solo color, sin pendiente.
    overlay = _math.calculate_macd_overlay(
        cerradas["close"].reset_index(drop=True), MATH_CFG)
    return bool(overlay.iloc[-1])


async def _procesar_top(bot: CanalBot, strat: SignalVWAP, top: list,
                        tf_ms: int, balance: float, trading: bool):
    """
    [11.3] Descarga OHLCV del TOP y evalua SignalVWAP simbolo a simbolo.
      Por cada senal en la ULTIMA vela (CODIGO-004):
        1) Genera el grafico estilo TradingView ([10A]).
        2) Envia la imagen a Telegram (siempre).
        3) Si trading_habilitado: abre la posicion con SL/TP ([8.6b]).
      Deduplica alertas repetidas de la misma vela (CODIGO-005).
    """
    try:
        ohlcv_data = await bot.fetch_ohlcv_batch(
            top, bot.cfg["timeframe"], bot.cfg["ohlcv_limit"])
    except RateLimitExceeded:
        flag("429", ORIGEN_BIBLIOTECA,
             "Escaneo: rate limit descargando velas.", logging.WARNING)
        return
    except Exception as e:
        flag("SCAN-UNEXPECTED", origen_de_excepcion(e),
             f"Descargando OHLCV batch: {e}", logging.ERROR)
        return

    if not ohlcv_data:
        flag("DATOS-006", ORIGEN_DATOS,
             "El batch de OHLCV vino vacio (rate limit o red).", logging.WARNING)
        return

    for symbol in top:
        if trading and symbol in bot.session_active:
            continue  # ya operado este simbolo
        # [FIX-3] Cooldown por simbolo (1h tras cerrar): se escribia en
        # manage_positions() pero NUNCA se leia. Ahora corta el escaneo
        # ANTES de descargar graficos/ alertas para ese simbolo.
        if trading and bot.en_cooldown_simbolo(symbol):
            log.debug(f"{symbol}: en cooldown por cierre reciente. Saltando.")
            continue
        data = ohlcv_data.get(symbol)
        if not data or len(data) < 20:
            flag("DATOS-007", ORIGEN_DATOS,
                 f"{symbol}: {0 if not data else len(data)} velas recibidas "
                 f"(minimo 20).", logging.DEBUG)
            continue

        try:
            df = pd.DataFrame(
                data,
                columns=["timestamp", "open", "high", "low", "close", "volume"])

            # --- Evaluar SignalVWAP en todo el historial descargado ---
            res = strat.evaluar(df, symbol)
            if res is None:
                continue

            # --- [FIX-2] Senal en la ULTIMA vela CERRADA (n-2) ---
            # Antes se evaluaba n-1 (vela aun viva): el cruce EMA9/EMA100
            # podia aparecer y desaparecer DENTRO de la misma vela (repaint)
            # y la orden salia por mecha, no por confirmacion. Ahora la
            # senal solo vale si la vela ya cerro y esta "fresca" (menos de
            # 2 escaneos), tolerando tanto el caso en que Bitget devuelve la
            # vela en formacion como el caso en que solo devuelve cerradas.
            ultima = res["n"] - 2
            lado = ("long" if res["long_mask"][ultima] else
                    "short" if res["short_mask"][ultima] else None)
            if not lado:
                continue

            # Vela de senal CERRADA: comprobar frescura de su cierre
            vela_ts = float(df["timestamp"].iloc[ultima])   # apertura
            cierre_ts = vela_ts + tf_ms                      # cierre real
            ahora = time.time() * 1000
            frescura_ms = max(tf_ms, int(bot.cfg["scan_interval_sec"]) * 2000)
            if not (cierre_ts <= ahora <= cierre_ts + frescura_ms):
                log.debug(f"{symbol} senal {lado.upper()} descartada: la vela "
                          f"de senal cerro hace demasiado tiempo.")
                continue

            # --- [11.3b] FILTRO MACD 15m: condicion de ENTRADA obligatoria ---
            #  LONG solo si Signal Line VERDE en 15m; SHORT solo si ROJA.
            #  Se evalua ANTES del dedupe: si hoy no cumple, la misma vela de
            #  senal puede cumplirla en el proximo escaneo (color 15m cambio).
            macd_ok = await _macd_color_15m(bot, symbol)
            if macd_ok is None:
                # Sin verificacion -> NO hay senal (no se inventa confirmacion)
                log.debug(f"{symbol} senal {lado.upper()} descartada: color "
                          f"MACD {bot.cfg.get('macd_filter_tf')} no evaluable.")
                continue
            if macd_ok != (lado == "long"):
                # long exige verde (True) / short exige rojo (False)
                log.debug(f"{symbol} senal {lado.upper()} descartada: Signal "
                          f"Line MACD {bot.cfg.get('macd_filter_tf')} "
                          f"{'VERDE' if macd_ok else 'ROJA'} no coincide.")
                continue

            # --- Deduplicar (CODIGO-005): misma vela + mismo lado = 1 aviso ---
            clave = f"{symbol}|{lado}|{int(vela_ts)}"
            if clave in bot.alertas_enviadas:
                continue
            bot.alertas_enviadas[clave] = time.time()
            # Limpiar claves viejas (> 48h) para no crecer en RAM
            limite = time.time() - 172_800
            bot.alertas_enviadas = {
                k: v for k, v in bot.alertas_enviadas.items() if v > limite}

            # --- Datos de la senal para SL/TP y grafico ---
            close_last = float(df["close"].iloc[ultima])
            vwap_last = float(res["vwap"][ultima])
            canal_up = res["canal"]["upper"][ultima]
            canal_lo = res["canal"]["lower"][ultima]

            # --- 1) Grafico estilo TradingView -> Telegram ---
            buf = await asyncio.to_thread(
                GraficoSignalVWAP.generar, symbol, df, res, bot.cfg,
                lado, ultima, True)
            caption = (
                f"*{symbol.split(':')[0]}* senal *{lado.upper()}* "
                f"(TF {bot.cfg['timeframe']})\n"
                f"Close: `{close_last:.6g}` | VWAP: `{vwap_last:.6g}`\n"
                f"Canal: sup `{canal_up:.6g}` / inf `{canal_lo:.6g}`\n"
                f"Ancla VWAP: {bot.cfg['vwap_anchor']}")
            if buf:
                enviado = await bot.send_telegram_photo(buf, caption)
                if not enviado:
                    # Fallback: si la foto falla, al menos mensaje de texto
                    await bot.send_telegram(caption)
                    flag("TG-PHOTO-FALLBACK", ORIGEN_BIBLIOTECA,
                         f"{symbol}: foto fallida, enviado solo texto.",
                         logging.WARNING)
            else:
                await bot.send_telegram(caption)
                flag("CHART-NULL", ORIGEN_CODIGO,
                     f"{symbol}: grafico None, enviado solo texto.",
                     logging.WARNING)

            log.info(f"SENAL {lado.upper()} {symbol} | close={close_last:.6g} "
                     f"| vwap={vwap_last:.6g}")

            # --- 2) Trading real (solo si trading_habilitado=True) ---
            if trading:
                # [FIX-1] Re-validar el cupo DENTRO del batch. can_open() se
                # evaluaba UNA vez antes de descargar las 100 velas: si el
                # mismo escaneo disparaba 5 senales, se abrian 5 posiciones
                # y se excedia max_open_positions (riesgo de sobreexposicion).
                if not await bot.can_open():
                    flag("RISK-MAXPOS", ORIGEN_CODIGO,
                         "Cupo de posiciones llenado durante el batch: "
                         "corto el escaneo de senales.", logging.WARNING)
                    break
                # [FIX-1b] Cooldown GLOBAL tambien se re-evalua por senal
                # (un cierre durante el batch puede haber disparado la pausa).
                if bot.is_on_cooldown():
                    flag("RISK-COOLDOWN", ORIGEN_CODIGO,
                         "Cooldown global disparado durante el batch: "
                         "corto el escaneo de senales.", logging.WARNING)
                    break
                # SL al ultimo pivot que toco SU borde; TP1/TP2 porcentuales
                # sobre la senal (sl_tp.calcular_sl_tp -> (sl, tp1, tp2)).
                sl, tp1, tp2 = _sl_tp_desde_canal(lado, close_last, canal_up,
                                                  canal_lo, bot.cfg,
                                                  res=res, df=df)
                abierta = await bot.open_position(
                    symbol=symbol, side=lado, sl_price=sl, tp_price=tp1,
                    tp2_price=tp2,
                    balance=balance, df=df, entry_idx=ultima,
                    vwap_value=vwap_last)
                if not abierta:
                    log.debug(f"{symbol}: posicion no abierta "
                              "(riesgo/limite/precison).")

        except RateLimitExceeded:
            flag("429", ORIGEN_BIBLIOTECA,
                 f"Procesando {symbol}: rate limit.", logging.WARNING)
            await asyncio.sleep(3)
        except Exception as e:
            flag("TOPITEM-UNEXPECTED", origen_de_excepcion(e),
                 f"Procesando {symbol}: {type(e).__name__}: {e}", logging.ERROR)
            continue


def _sl_tp_desde_canal(lado: str, precio: float, canal_up: float,
                       canal_lo: float, cfg: dict,
                       res: Optional[dict] = None,
                       df: Optional["pd.DataFrame"] = None):
    """
    [11.4] Wrapper de COMPATIBILIDAD -> sl_tp.calcular_sl_tp().
    Devuelve (sl, tp1, tp2): SL por pivote + TP1/TP2 porcentuales.
    Toda la logica de SL/TP/BE/trailing esta encapsulada en sl_tp.py:
    EDITAR AHI para cambiar el trading.
    Se mantiene este nombre porque lo usan el escaneo (_procesar_top), el
    grafico (GraficoSignalVWAP.generar), generar_ejemplos.py y los tests.
    """
    return sl_tp.calcular_sl_tp(lado, precio, canal_up, canal_lo, cfg,
                                res=res, df=df)


# =============================================================================
# [12] MAIN / ARRANQUE - registro de senales del SO y ejecucion
#      (main() es llamado tanto por `python canalBot.py` como por el
#       servicio web bot_web_service.py en su hilo BotRunner)
# =============================================================================
def _manejador_signal(signum, _frame):
    """[12.1] SIGTERM (Render lo envia al redesplegar) -> cierre ordenado."""
    flag("CODIGO-011", ORIGEN_CODIGO,
         f"Senal {signum} recibida del SO. Cerrando ordenadamente...",
         logging.WARNING)
    raise KeyboardInterrupt


def main():
    """[12.2] Punto de entrada unico: banner, senales del SO y bucle async."""
    # Senales SO: solo registrables desde el HILO PRINCIPAL (si se ejecuta
    # en un hilo (bot_web_service) se omite: el proceso lo cierra Render).
    if threading.current_thread() is threading.main_thread():
        try:
            signal_mod.signal(signal_mod.SIGTERM, _manejador_signal)
        except (ValueError, OSError, AttributeError) as e:
            flag("CODIGO-010", ORIGEN_CODIGO,
                 f"No se pudo registrar SIGTERM (plataforma?): {e}",
                 logging.WARNING)

    # Banner de arranque (via logging, jamas print)
    log.info("=" * 60)
    log.info(" canalBot - SignalVWAP + Canal de Regresion Logaritmica")
    log.info(f" modo: {'TRADING' if CONFIG.get('trading_habilitado') else 'ALERTAS'}"
             f" | TF={CONFIG['timeframe']} | ancla VWAP={CONFIG['vwap_anchor']}")
    log.info("=" * 60)

    _bot = CanalBot(CONFIG)
    _strat = SignalVWAP(_bot.cfg)
    asyncio.run(loop_principal(_bot, _strat))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Saliendo (KeyboardInterrupt).")
    except Exception as e:
        flag("FATAL", origen_de_excepcion(e),
             f"FATAL no controlado: {type(e).__name__}: {e}", logging.CRITICAL)
        resumen_flags()
        raise
