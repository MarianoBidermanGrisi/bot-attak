# -*- coding: utf-8 -*-
"""
[MODULE SL/TP] sl_tp.py - TODA la logica de Stop Loss y Take Profit.

    ###############################################################
    #  EDITAR ESTE FICHERO PARA MODIFICAR EL TRADING DE SL Y TP  #
    ###############################################################

SECCIONES (logica PURA: solo numeros, sin I/O):
  [1] ENTRADA  - calcular_sl_tp()   : SL + TP1/TP2 al dispararse la senal
                           validar_entrada() : validacion + reescala al
                                       precio REAL de entrada de Bitget
  [1b] TP1/TP2 - tp_accion()        : ¿cierro 50% (TP1) o todo (TP2)?
                    split_tp_qty()   : parte la posicion en cantidades
                                       TP1/TP2 para colocar las ordenes
                                       profit_plan EN Bitget [TPO-3]
  [2] BREAK EVEN - be_debe_evaluar() / be_objetivo() / be_mejora()
  [3] TRAILING   - trail_activacion() / trail_objetivo() / trail_debe_mover()
                   (logica CONSERVADA; se activa con trailing_enabled=True)

[TPO-2] EL TP ESTRUCTURAL (pivote/borde/rr_ratio/CODIGO-017) FUE ELIMINADO.
  Ahora hay DOS TPs porcentuales fijos sobre la entrada:
    TP1 = +/- tp1_pct (2%)  -> el llamador cierra tp1_close_frac (50%)
    TP2 = +/- tp2_pct (3%)  -> el llamador cierra el RESTO (posicion total)

QUE NO ESTA AQUI (sigue en canalBot.py - NO tocar):
  * Ordenes a Bitget (_update_stop_loss, create_order, cierres parciales),
    sizing, precision.
  * Estado de posiciones (trade_entries / alerts_history), logs/flags,
    mensajes Telegram, variables de entorno y despliegue en Render.

CONTRATO DE ENTRADA/SALIDA (los wrappers de canalBot.py dependen de esto):
  * calcular_sl_tp(...)  -> (sl: float, tp1: float, tp2: float)
  * validar_entrada(...) -> {"sl","tp1","tp2","sl_dist","tp1_dist","tp2_dist"}
                             | None = invalido; ya se emitio el flag
                             (PRICE-NAN, SL-INVALID, TP-INVALID) y el
                             llamador NO debe abrir posicion.
  * tp_accion(profit, tp1_done, cfg) -> "tp2" | "tp1" | None (decision pura)
  * split_tp_qty(qty, frac, step)    -> (tp1_qty, tp2_qty) pura [TPO-3]
  * be_debe_evaluar(profit, cfg, tp1_done) -> bool
  * be_mejora(...) / trail_debe_mover(...) -> bool (decision pura)
  * trail_activacion(...) -> {"api": bool, "sl": float} | None
      None = aun no activa; api=True -> enviar orden ahora;
      api=False -> marcar estado y ajustar en el proximo tick.

flag() se importa de forma PEREZOSA desde canalBot dentro de las funciones
que lo usan: canalBot.py importa este modulo en sus imports de cabecera,
de modo que un import en frio aqui crearia un import circular.
"""
import math
import logging
from typing import Optional

import numpy as np

# MISMO logger que canalBot (logging.getLogger("canalbot"))
log = logging.getLogger("canalbot")


# =============================================================================
# [1] ENTRADA - SL/TP de la senal (se calculan UNA vez, al abrir)
# =============================================================================
def calcular_sl_tp(lado: str, precio: float, canal_up: float,
                   canal_lo: float, cfg: dict,
                   res: Optional[dict] = None,
                   df: Optional["pd.DataFrame"] = None):
    """
    [11.4] SL/TP de la senal SignalVWAP (configurable en CONFIG [3.6]).

    STOP LOSS anclado al ULTIMO PIVOT que toco el borde del canal:
      LONG (parte BAJA del canal): se recorre la historia hacia atras hasta
        encontrar el pivot SWING LOW cuyo minimo toco (o cruzo) el borde
        inferior del canal -> SL = minimo de ese pivot * (1 - sl_buffer_pct),
        es decir "un poco por debajo" del pivot.
      SHORT (parte ALTA del canal): se busca el pivot SWING HIGH cuyo maximo
        toco el borde superior -> SL = maximo * (1 + sl_buffer_pct),
        "un poco por encima" del pivot.
      Fallback (ningun pivot llego a tocar el borde): se usa el borde del
        canal del momento con el mismo buffer (comportamiento anterior).
      Distancia SL recortada a [min_sl_dist_pct, sl_max_dist_pct]
      (CODIGO-009).

    TAKE PROFIT - DOS objetivos porcentuales FIJOS sobre la entrada
      [TPO-2] el TP estructural (pivote opuesto / borde / rr_ratio /
      CODIGO-017) fue ELIMINADO por decision del usuario:
        LONG  : tp1 = precio * (1 + tp1_pct)   (2% -> el llamador cierra 50%)
                tp2 = precio * (1 + tp2_pct)   (3% -> el llamador cierra TODO)
        SHORT : tp1 = precio * (1 - tp1_pct)
                tp2 = precio * (1 - tp2_pct)
      No dependen del canal, de pivotes ni del SL.
    """
    from canalBot import flag, ORIGEN_CODIGO   # lazy: evita import circular
    tol = cfg.get("pivot_touch_tol", 0.005)
    sl_raw = None   # precio de referencia del SL (None -> fallback al canal)

    # --- Buscar hacia atras el ultimo pivot que toco el borde del canal ---
    if res is not None and df is not None:
        est = res.get("estructura", {})
        canal = res.get("canal", {})
        if lado == "long":
            barras = est.get("piv_lo_bar")       # barra del pivot bajo vigente
            borde = canal.get("lower")           # borde inferior del canal
            minima = df["low"].values            # minimo de cada vela
        else:
            barras = est.get("piv_hi_bar")       # barra del pivot alto vigente
            borde = canal.get("upper")           # borde superior del canal
            minima = df["high"].values           # maximo de cada vela
        if barras is not None and borde is not None:
            visto = -2
            for t in range(len(barras) - 1, -1, -1):
                pb = int(barras[t])
                if pb < 0 or pb == visto:        # sin pivot / ya evaluado
                    continue
                visto = pb
                b = borde[pb] if 0 <= pb < len(borde) else np.nan
                if not np.isfinite(b):
                    continue                     # el canal aun no existia
                toco = (minima[pb] <= b * (1 + tol) if lado == "long"
                        else minima[pb] >= b * (1 - tol))
                if toco:
                    if lado == "long":
                        sl_raw = float(minima[pb]) * (1 - cfg["sl_buffer_pct"])
                    else:
                        sl_raw = float(minima[pb]) * (1 + cfg["sl_buffer_pct"])
                    log.debug(f"SL por pivot: barra {pb} toco el borde "
                              f"({lado}); sl_raw={sl_raw:.6g}")
                    break

    # --- Valor del SL: pivot encontrado o fallback al borde del canal ---
    if lado == "long":
        if sl_raw is None:
            sl_raw = canal_lo if (np.isfinite(canal_lo) and canal_lo < precio) \
                else precio * (1 - cfg["min_sl_dist_pct"])
            sl_raw *= (1 - cfg["sl_buffer_pct"])   # buffer bajo el borde
        dist = (precio - sl_raw) / precio
    else:
        if sl_raw is None:
            sl_raw = canal_up if (np.isfinite(canal_up) and canal_up > precio) \
                else precio * (1 + cfg["min_sl_dist_pct"])
            sl_raw *= (1 + cfg["sl_buffer_pct"])   # buffer sobre el borde
        dist = (sl_raw - precio) / precio

    # Recortar a rangos validos (CODIGO-009 si queda invalido)
    dist = max(cfg["min_sl_dist_pct"], min(dist, cfg["sl_max_dist_pct"]))
    if not np.isfinite(dist) or dist <= 0:
        flag("CODIGO-009", ORIGEN_CODIGO,
             f"SL invalido para {lado}@{precio:.6g}; uso min_sl_dist_pct.",
             logging.WARNING)
        dist = cfg["min_sl_dist_pct"]

    if lado == "long":
        sl = precio * (1 - dist)
    else:
        sl = precio * (1 + dist)

    # ============ TP1 / TP2: porcentuales FIJOS sobre la entrada ============
    # [TPO-2] Sin dependencia del canal ni de pivotes: ver docstring.
    if lado == "long":
        tp1 = precio * (1 + cfg["tp1_pct"])
        tp2 = precio * (1 + cfg["tp2_pct"])
    else:
        tp1 = precio * (1 - cfg["tp1_pct"])
        tp2 = precio * (1 - cfg["tp2_pct"])
    log.debug(f"SL/TP calculados {lado}@{precio:.6g}: sl={sl:.6g} "
              f"tp1={tp1:.6g} (+/-{cfg['tp1_pct']*100:.0f}%) "
              f"tp2={tp2:.6g} (+/-{cfg['tp2_pct']*100:.0f}%)")
    return sl, tp1, tp2


# =============================================================================
# [1b] TP1/TP2 - decision pura del cierre parcial / total
# =============================================================================
def tp_accion(profit_pct: float, tp1_done: bool, cfg: dict) -> Optional[str]:
    """
    ¿Que cierre toca ahora? (el llamador ejecuta la orden).

      "tp2"  -> profit >= tp2_pct: cierre TOTAL de la posicion. Tambien si
                el precio SALTO directo a TP2 sin pasar por TP1 (gap entre
                ticks): TP2 siempre cierra todo, aunque tp1_done=False.
      "tp1"  -> profit >= tp1_pct y TP1 aun no ejecutado: cerrar
                tp1_close_frac (50%) de la posicion.
      None   -> aun no se llega a TP1 (o TP1 ya ejecutado y < TP2).

    profit_pct no finito (NaN/Inf) -> None (nunca cierra con datos rotos).
    """
    if not math.isfinite(profit_pct):
        return None
    if profit_pct >= cfg["tp2_pct"]:
        return "tp2"
    if not tp1_done and profit_pct >= cfg["tp1_pct"]:
        return "tp1"
    return None


def split_tp_qty(qty: float, frac: float, step: float):
    """
    [TPO-3] Parte la posicion en (tp1_qty, tp2_qty) para colocar las DOS
    ordenes profit_plan en Bitget (TP1 cierra `frac`, TP2 cierra el resto).

      * tp1 = floor(qty*frac/step)*step  -> alineado al step, <= frac
      * tp2 = qty - tp1                  -> SUMA EXACTA = qty (cumple la
        regla de Bitget: la suma de los TP no puede superar el volumen
        de la posicion)
      * qty no divisible en 2 pasos (qty < step), o entradas no finitas
        -> (0.0, qty): el llamador NO coloca TP1 y usa fallback completo
        (canalBot._place_exchange_tps es all-or-nothing).
      * frac fuera de rango se recorta a [0, 1] (nunca tp2 negativo).
    """
    if not all(math.isfinite(v) for v in (qty, frac, step)) \
            or not (step > 0) or not (qty > 0):
        return 0.0, 0.0
    frac_c = max(0.0, min(frac, 1.0))
    steps = int(math.floor((qty * frac_c) / step + 1e-9))   # tol fp
    tp1 = min(max(steps, 0) * step, qty)
    tp1 = round(tp1, 12)
    tp2 = round(qty - tp1, 12)
    if tp1 < step:                       # no se puede partir (o frac=0)
        return 0.0, round(qty, 12)
    return tp1, tp2


def validar_entrada(symbol: str, side: str, strategy_entry: float,
                     price: float, sl_price: float, tp1_price: float,
                     tp2_price: float) -> Optional[dict]:
    """
    [11.5] Validacion + reescalado de SL/TP1/TP2 al precio REAL de entrada.
      1) NaN/Inf en cualquier precio -> None (flag PRICE-NAN).
      2) Distancias respecto a la senal (close de la vela):
         SL en (0%, 10%] -> si no, None (flag SL-INVALID; 10% < liquidacion).
         tp1_dist > 0 y tp2_dist > tp1_dist -> si no, None (TP-INVALID).
      3) Re-escala esas DISTANCIAS al precio real de mercado (price), para
         no perder la relacion porcentual senal -> fill real.
      4) TP1/TP2 deben quedar del lado correcto del precio -> si no, None.
    Devuelve {"sl","tp1","tp2","sl_dist","tp1_dist","tp2_dist"} o None.
    """
    from canalBot import flag, ORIGEN_CODIGO, ORIGEN_DATOS  # lazy

    # --- Validacion de numericidad (NaN/Inf) ---
    if not all(math.isfinite(v) for v in [sl_price, tp1_price, tp2_price,
                                          strategy_entry, price]):
        flag("PRICE-NAN", ORIGEN_DATOS,
             f"{symbol}: precio no finito (NaN/Inf). Saltando.", logging.WARNING)
        return None

    # --- Distancias SL/TP respecto a la senal (validar antes de usar). ---
    if side == "long":
        sl_dist = (strategy_entry - sl_price) / strategy_entry
        tp1_dist = (tp1_price - strategy_entry) / strategy_entry
        tp2_dist = (tp2_price - strategy_entry) / strategy_entry
    else:
        sl_dist = (sl_price - strategy_entry) / strategy_entry
        tp1_dist = (strategy_entry - tp1_price) / strategy_entry
        tp2_dist = (strategy_entry - tp2_price) / strategy_entry
    if sl_dist <= 0 or sl_dist > 0.10:
        flag("SL-INVALID", ORIGEN_CODIGO,
             f"{symbol}: SL invalido ({sl_dist*100:.1f}%). Saltando.",
             logging.WARNING)
        return None
    if tp1_dist <= 0 or tp2_dist <= tp1_dist:
        flag("TP-INVALID", ORIGEN_CODIGO,
             f"{symbol}: TP1/TP2 no validos (tp1={tp1_dist*100:.2f}% "
             f"tp2={tp2_dist*100:.2f}%; exige 0 < tp1 < tp2). Saltando.",
             logging.WARNING)
        return None

    # --- Recalcular SL/TP1/TP2 con el precio real de entrada ---
    if side == "long":
        sl_price = price * (1 - sl_dist)
        tp1_price = price * (1 + tp1_dist)
        tp2_price = price * (1 + tp2_dist)
    else:
        sl_price = price * (1 + sl_dist)
        tp1_price = price * (1 - tp1_dist)
        tp2_price = price * (1 - tp2_dist)

    # --- Validar TP1/TP2 vs precio actual (lado correcto) ---
    if side == "long" and not (price < tp1_price < tp2_price):
        flag("TP-INVALID", ORIGEN_CODIGO,
             f"{symbol}: LONG: TP1 ({tp1_price:.4f}) / TP2 ({tp2_price:.4f}) "
             f"no quedan sobre el precio ({price:.4f}).",
             logging.WARNING)
        return None
    if side == "short" and not (price > tp1_price > tp2_price):
        flag("TP-INVALID", ORIGEN_CODIGO,
             f"{symbol}: SHORT: TP1 ({tp1_price:.4f}) / TP2 ({tp2_price:.4f}) "
             f"no quedan bajo el precio ({price:.4f}).",
             logging.WARNING)
        return None

    return {"sl": sl_price, "tp1": tp1_price, "tp2": tp2_price,
            "sl_dist": sl_dist, "tp1_dist": tp1_dist, "tp2_dist": tp2_dist}


# =============================================================================
# [2] BREAK EVEN - se evalua UNA sola vez por posicion (logica pura)
# =============================================================================
def be_debe_evaluar(profit_pct: float, cfg: dict,
                    tp1_done: bool = True) -> bool:
    """[11.6a] ¿El precio va a favor lo suficiente como para evaluar el BE?

    [BE-TP1] Si cfg["be_after_tp1"] es True, el BE SOLO se evalua DESPUES
    de ejecutarse el TP1 (tp1_done=True). Antes del TP1 el BE no existe.
    (el llamador decide si ya fue gestionado con alerts_history)."""
    if cfg.get("be_after_tp1", False) and not tp1_done:
        return False
    return profit_pct >= cfg["be_trigger_pct"]


def be_objetivo(side: str, entry: float, cfg: dict) -> float:
    """[11.6b] SL del BREAK EVEN: entrada +/- be_offset_pct.
    LONG -> entrada * (1 + offset) / SHORT -> entrada * (1 - offset)."""
    if side == "long":
        return entry * (1 + cfg["be_offset_pct"])
    return entry * (1 - cfg["be_offset_pct"])


def be_mejora(side: str, new_sl: float, sl_actual: float) -> bool:
    """[11.6c] ¿El BE MEJORA el stop actual? (nunca empeorar el riesgo).
      LONG : subir el stop  -> new_sl > sl_actual
      SHORT: bajar el stop  -> new_sl < sl_actual
    Si no mejora: NO se llama a la API (se ahorra rate limit) y se marca
    el BE como gestionado."""
    if side == "long":
        mejora = new_sl > sl_actual
    else:
        mejora = new_sl < sl_actual
    return mejora


# =============================================================================
# [3] TRAILING STOP - logica pura; el llamador ejecuta API/estado/logs
# =============================================================================
def trail_activacion(side: str, entry: float, original_sl: Optional[float],
                     mark: float) -> Optional[dict]:
    """
    [11.7a] Activacion del trailing a ratio 1:1 con el SL original.
      LONG : activa cuando mark >= entry + (entry - original_sl);
             SL inicial = entry + (entry - original_sl).
      SHORT: activa cuando mark <= entry - (original_sl - entry);
             SL inicial = entry - (original_sl - entry).
      Devuelve None  -> todavia no se activa.
             {"api": True,  "sl": X} -> SL ya valido (no cruza el precio):
                                        enviar orden AHORA.
             {"api": False, "sl": X} -> SL cruzaria el precio: marcar activo
                                        y ajustar en el proximo tick.
      original_sl=None (sin dato de la entrada) -> None.
    """
    if original_sl is None:
        return None
    if side == "long":
        sl_dist = entry - original_sl          # riesgo por unidad
        activation_price = entry + sl_dist     # objetivo 1:1
        if mark >= activation_price:
            initial_sl = entry + sl_dist
            if initial_sl < mark:              # SL no cruza el precio
                return {"api": True, "sl": initial_sl}
            return {"api": False, "sl": entry - sl_dist}
        return None

    sl_dist = original_sl - entry
    activation_price = entry - sl_dist
    if mark <= activation_price:
        initial_sl = entry - sl_dist
        if initial_sl > mark:
            return {"api": True, "sl": initial_sl}
        return {"api": False, "sl": entry + sl_dist}
    return None


def trail_objetivo(side: str, mark: float, cfg: dict) -> float:
    """[11.7b] SL objetivo del trailing anclado al PICO (distancia fija):
      trailing_dist_pct (0.35%) = distancia real SL <-> pico."""
    trail_dist = cfg["trailing_dist_pct"]
    if side == "long":
        objetivo = mark * (1 - trail_dist)      # pico - 0.35%
    else:
        objetivo = mark * (1 + trail_dist)      # pico + 0.35%
    return objetivo


def trail_debe_mover(side: str, objetivo: float, sl_actual: float,
                     mark: float, cfg: dict) -> bool:
    """[11.7c] ¿Se envia la orden de trailing?
      * trailing_step_pct (0.3%) = mejora MINIMA exigida antes de llamar a
        la API (evita spam de ordenes y 429).
      * el SL jamas cruza el precio actual (objetivo < mark en LONG).
      * monotonia: jamas empeora el stop (mejora calculada vs sl_actual).
    """
    min_mejora = cfg["trailing_step_pct"]
    if sl_actual <= 0:
        mejora = 1.0                                # sin SL previo: siempre
    elif side == "long":
        mejora = (objetivo - sl_actual) / sl_actual
    else:
        mejora = (sl_actual - objetivo) / sl_actual

    if side == "long":
        return objetivo < mark and mejora >= min_mejora
    return objetivo > mark and mejora >= min_mejora
