# bot_web_service.py

# --- INICIO: BLOQUE DE DIAGNÓSTICO ---
import sys
import traceback
print("="*50)
print("🔍 SCRIPT STARTED: bot_web_service.py")
print(f"🔍 Python Version: {sys.version}")
print("🔍 Starting imports...")
# --- FIN: BLOQUE DE DIAGNÓSTICO ---

import requests
import time
import json
import os
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
import matplotlib
matplotlib.use('Agg')  # Backend sin GUI
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from io import BytesIO
from flask import Flask, request, jsonify
import threading
import logging
from telegram import Bot
from telegram.error import TelegramError

# --- INICIO: BLOQUE DE DIAGNÓSTICO ---
print("🔍 All imports completed successfully.")
# --- FIN: BLOQUE DE DIAGNÓSTICO ---

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# Optimizador IA (Mejorado)
# ---------------------------
class OptimizadorIA:
    def __init__(self, log_path="operaciones_log.csv", min_samples=15):
        self.log_path = log_path
        self.min_samples = min_samples
        self.datos = self.cargar_datos()

    def cargar_datos(self):
        datos = []
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        pnl = float(row.get('pnl_percent', 0))
                        angulo = float(row.get('angulo_tendencia', 0))
                        pearson = float(row.get('pearson', 0))
                        r2 = float(row.get('r2_score', 0))
                        ancho_relativo = float(row.get('ancho_canal_relativo', 0))
                        nivel_fuerza = int(row.get('nivel_fuerza', 1))
                        datos.append({
                            'pnl': pnl, 
                            'angulo': angulo, 
                            'pearson': pearson, 
                            'r2': r2,
                            'ancho_relativo': ancho_relativo,
                            'nivel_fuerza': nivel_fuerza
                        })
                    except Exception:
                        continue
        except FileNotFoundError:
            logger.warning("⚠ No se encontró operaciones_log.csv (optimizador)")
        return datos

    def evaluar_configuracion(self, trend_threshold, min_strength, entry_margin):
        if not self.datos:
            return -99999
            
        filtradas = [
            op for op in self.datos
            if abs(op['angulo']) >= trend_threshold
            and abs(op['angulo']) >= min_strength
            and abs(op['pearson']) >= 0.4
            and op.get('nivel_fuerza', 1) >= 2
            and op.get('r2', 0) >= 0.4
        ]
        
        n = len(filtradas)
        if n < max(8, int(0.15 * len(self.datos))):
            return -10000 - n
            
        pnls = [op['pnl'] for op in filtradas]
        pnl_mean = statistics.mean(pnls) if filtradas else 0
        pnl_std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        
        winrate = sum(1 for op in filtradas if op['pnl'] > 0) / n if n > 0 else 0
        
        score = (pnl_mean - 0.5 * pnl_std) * winrate * math.sqrt(n)
        
        ops_calidad = [op for op in filtradas if op.get('r2', 0) >= 0.6 and op.get('nivel_fuerza', 1) >= 3]
        if ops_calidad:
            score *= 1.2
            
        return score

    def buscar_mejores_parametros(self):
        if not self.datos or len(self.datos) < self.min_samples:
            logger.info(f"ℹ️ No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
            
        mejor_score = -1e9
        mejores_param = None
        
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        logger.info(f"🔎 Optimizador: probando {total} combinaciones...")
        
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if idx % 100 == 0 or idx == total:
                logger.info(f"   · probado {idx}/{total} combos (mejor score actual: {mejor_score:.4f})")
            if score > mejor_score:
                mejor_score = score
                mejores_param = {
                    'trend_threshold_degrees': t,
                    'min_trend_strength_degrees': s,
                    'entry_margin': m,
                    'score': score,
                    'evaluated_samples': len(self.datos),
                    'total_combinations': total
                }
                
        if mejores_param:
            logger.info("✅ Optimizador: mejores parámetros encontrados: " + str(mejores_param))
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                logger.warning(f"⚠ Error guardando mejores_parametros.json: {e}")
        else:
            logger.warning("⚠ No se encontró una configuración mejor")
            
        return mejores_param

# ---------------------------
# BOT PRINCIPAL (MEJORADO)
# ---------------------------
class TradingBot:
    def __init__(self, auto_optimize=False, log_path="operaciones_log.csv"):
        logger.info("🔍 Initializing TradingBot...")
        self.log_path = log_path
        self.auto_optimize = auto_optimize
        
        # Obtener configuración de variables de entorno
        self.telegram_token = os.environ.get('TELEGRAM_TOKEN')
        telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_IDS', '')
        self.telegram_chat_ids = [chat_id.strip() for chat_id in telegram_chat_ids_str.split(',') if chat_id.strip()]
        
        logger.info(f"🔍 Telegram Token configured: {'Yes' if self.telegram_token else 'No'}")
        logger.info(f"🔍 Telegram Chat IDs configured: {self.telegram_chat_ids}")
        
        # Inicializar bot de Telegram
        self.telegram_bot = None
        if self.telegram_token:
            try:
                self.telegram_bot = Bot(token=self.telegram_token)
                logger.info("🔍 Telegram Bot initialized successfully.")
            except Exception as e:
                logger.error(f"Error inicializando bot de Telegram: {e}")
        
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.total_operaciones = 0
        
        # Nuevo: historial de breakouts
        self.breakout_history = {}
        
        # Estado del bot
        self.is_running = False
        self.bot_thread = None

        # Configuración automática con los parámetros especificados
        logger.info("🔧 Cargando configuración predefinida...")
        self.config = {
            'candle_period': 390,
            'interval': '3m',
            'trend_threshold_degrees': 15,
            'entry_margin': 0.01,
            'min_rr_ratio': 1.5,
            'scan_interval_minutes': 1,
            'min_trend_strength_degrees': 15,
            'min_channel_width': 1.0,
            'symbols': [
                'BTCUSDT','ETHUSDT','ADAUSDT','DOTUSDT','LINKUSDT','BNBUSDT','XRPUSDT','SOLUSDT','MATICUSDT','AVAXUSDT',
                'DOGEUSDT','LTCUSDT','ATOMUSDT','UNIUSDT','XLMUSDT','ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT','ETCUSDT',
                'BCHUSDT','EOSUSDT','XMRUSDT','TRXUSDT','XTZUSDT','AAVEUSDT','SUSHIUSDT','MKRUSDT','COMPUSDT','YFIUSDT',
                'SNXUSDT','CRVUSDT','RENUSDT','1INCHUSDT','OCEANUSDT','BANDUSDT','NEOUSDT','QTUMUSDT','ZILUSDT','HOTUSDT',
                'ENJUSDT','MANAUSDT','BATUSDT','ZRXUSDT','OMGUSDT'
            ]
        }
        
        # Guardar la configuración aplicada
        try:
            with open("parametros_predefinidos.json", "w", encoding='utf-8') as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()
        logger.info("🔍 TradingBot initialization complete.")

    def reoptimizar_periodicamente(self):
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 3600
            
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= 4:
                logger.info("🔄 Iniciando re-optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=12)
                nuevos_parametros = ia.buscar_mejores_parametros()
                
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    logger.info("✅ Parámetros actualizados en tiempo real")
                    
        except Exception as e:
            logger.error(f"⚠ Error en re-optimización automática: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', self.config['trend_threshold_degrees'])
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', self.config['min_trend_strength_degrees'])
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', self.config['entry_margin'])
        
        try:
            with open("parametros_actualizados.json", "w", encoding='utf-8') as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    async def _enviar_telegram_async(self, mensaje, chat_id):
        """Envía mensaje usando python-telegram-bot de forma asíncrona"""
        if not self.telegram_bot:
            return False
        
        try:
            await self.telegram_bot.send_message(
                chat_id=chat_id,
                text=mensaje,
                parse_mode='HTML'
            )
            return True
        except TelegramError as e:
            logger.error(f"Error enviando mensaje a Telegram: {e}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado enviando a Telegram: {e}")
            return False

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Método de respaldo usando requests"""
        if not token or not chat_ids:
            return False
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                r = requests.post(url, json=payload, timeout=10)
                resultados.append(r.status_code == 200)
            except Exception:
                resultados.append(False)
        return any(resultados)

    def enviar_telegram(self, mensaje):
        """Envía mensaje a todos los chats configurados"""
        if not self.telegram_token or not self.telegram_chat_ids:
            logger.warning("No hay token o chat IDs configurados para Telegram")
            return False
        
        # Intentar usar python-telegram-bot primero
        if self.telegram_bot:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def enviar_a_todos():
                    resultados = []
                    for chat_id in self.telegram_chat_ids:
                        resultado = await self._enviar_telegram_async(mensaje, chat_id)
                        resultados.append(resultado)
                    return any(resultados)
                
                resultado = loop.run_until_complete(enviar_a_todos())
                loop.close()
                return resultado
            except Exception as e:
                logger.error(f"Error con python-telegram-bot, usando método de respaldo: {e}")
        
        # Método de respaldo con requests
        return self._enviar_telegram_simple(mensaje, self.telegram_token, self.telegram_chat_ids)

    def inicializar_log(self):
        if not os.path.exists(self.archivo_log):
            with open(self.archivo_log, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'tipo', 'precio_entrada',
                    'take_profit', 'stop_loss', 'precio_salida',
                    'resultado', 'pnl_percent', 'duracion_minutos',
                    'angulo_tendencia', 'pearson', 'r2_score',
                    'ancho_canal_relativo',
                    'nivel_fuerza',
                    'rango_velas_entrada',
                    'stoch_k',
                    'stoch_d'
                ])

    def registrar_operacion(self, datos_operacion):
        with open(self.archivo_log, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datos_operacion['timestamp'],
                datos_operacion['symbol'],
                datos_operacion['tipo'],
                datos_operacion['precio_entrada'],
                datos_operacion['take_profit'],
                datos_operacion['stop_loss'],
                datos_operacion['precio_salida'],
                datos_operacion['resultado'],
                datos_operacion['pnl_percent'],
                datos_operacion['duracion_minutos'],
                datos_operacion['angulo_tendencia'],
                datos_operacion['pearson'],
                datos_operacion['r2_score'],
                datos_operacion.get('ancho_canal_relativo', 0),
                datos_operacion.get('nivel_fuerza', 1),
                datos_operacion.get('rango_velas_entrada', 0),
                datos_operacion.get('stoch_k', 0),
                datos_operacion.get('stoch_d', 0)
            ])

    def verificar_cierre_operaciones(self):
        if not self.operaciones_activas:
            return []
        operaciones_cerradas = []
        for simbolo, operacion in list(self.operaciones_activas.items()):
            datos = self.obtener_datos_mercado(simbolo)
            if not datos:
                continue
            precio_actual = datos['precio_actual']
            tp = operacion['take_profit']
            sl = operacion['stop_loss']
            tipo = operacion['tipo']
            resultado = None
            if tipo == "LONG":
                if precio_actual >= tp:
                    resultado = "TP"
                elif precio_actual <= sl:
                    resultado = "SL"
            else:
                if precio_actual <= tp:
                    resultado = "TP"
                elif precio_actual >= sl:
                    resultado = "SL"
            if resultado:
                if tipo == "LONG":
                    pnl_percent = ((precio_actual - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
                else:
                    pnl_percent = ((operacion['precio_entrada'] - precio_actual) / operacion['precio_entrada']) * 100
                tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
                duracion_minutos = (datetime.now() - tiempo_entrada).total_seconds() / 60
                
                datos_operacion = {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': simbolo,
                    'tipo': tipo,
                    'precio_entrada': operacion['precio_entrada'],
                    'take_profit': tp,
                    'stop_loss': sl,
                    'precio_salida': precio_actual,
                    'resultado': resultado,
                    'pnl_percent': pnl_percent,
                    'duracion_minutos': duracion_minutos,
                    'angulo_tendencia': operacion.get('angulo_tendencia', 0),
                    'pearson': operacion.get('pearson', 0),
                    'r2_score': operacion.get('r2_score', 0),
                    'ancho_canal_relativo': operacion.get('ancho_canal_relativo', 0),
                    'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                    'rango_velas_entrada': operacion.get('rango_velas_entrada', 0),
                    'stoch_k': operacion.get('stoch_k', 0),
                    'stoch_d': operacion.get('stoch_d', 0)
                }
                
                mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                self.enviar_telegram(mensaje_cierre)
                self.registrar_operacion(datos_operacion)
                operaciones_cerradas.append(simbolo)
                del self.operaciones_activas[simbolo]
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
                
                self.operaciones_desde_optimizacion += 1
                self.total_operaciones += 1
                logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
                
                self.reoptimizar_periodicamente()
                
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>

{color_emoji} <b>RESULTADO: {datos_operacion['resultado']}</b>

📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}

💵 PnL Absoluto: {pnl_absoluto:.8f}
📈 PnL %: {datos_operacion['pnl_percent']:.2f}%
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos

📐 Ángulo Tendencia: {datos_operacion['angulo_tendencia']:.1f}°
📊 Pearson: {datos_operacion['pearson']:.3f}
🎯 R² Score: {datos_operacion['r2_score']:.3f}
📊 Stoch K: {datos_operacion.get('stoch_k', 0):.1f}
📈 Stoch D: {datos_operacion.get('stoch_d', 0):.1f}
🕒 {datos_operacion['timestamp']}
        """
        return mensaje

    def obtener_datos_mercado(self, simbolo):
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': simbolo, 'interval': self.config['interval'], 'limit': self.config['candle_period'] + 14}
        try:
            respuesta = requests.get(url, params=params, timeout=10)
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                raise ValueError("Respuesta inválida de Binance")
            maximos = []
            minimos = []
            cierres = []
            tiempos = []
            for i, vela in enumerate(datos):
                maximos.append(float(vela[2]))
                minimos.append(float(vela[3]))
                cierres.append(float(vela[4]))
                tiempos.append(i)
            self.ultimos_datos[simbolo] = {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'tiempos': tiempos,
                'precio_actual': cierres[-1] if cierres else 0
            }
            return self.ultimos_datos[simbolo]
        except Exception as e:
            logger.error(f"❌ Error obteniendo {simbolo}: {e}")
            return None

    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        """Calcula el indicador Stochástico"""
        if len(datos_mercado['cierres']) < period:
            return 50, 50
            
        cierres = datos_mercado['cierres']
        maximos = datos_mercado['maximos']
        minimos = datos_mercado['minimos']
        
        # Calcular %K
        k_values = []
        for i in range(period-1, len(cierres)):
            highest_high = max(maximos[i-period+1:i+1])
            lowest_low = min(minimos[i-period+1:i+1])
            if highest_high == lowest_low:
                k = 50
            else:
                k = 100 * (cierres[i] - lowest_low) / (highest_high - lowest_low)
            k_values.append(k)
        
        # Calcular %K suavizado
        if len(k_values) >= k_period:
            k_smoothed = []
            for i in range(k_period-1, len(k_values)):
                k_avg = sum(k_values[i-k_period+1:i+1]) / k_period
                k_smoothed.append(k_avg)
            
            # Calcular %D
            if len(k_smoothed) >= d_period:
                d = sum(k_smoothed[-d_period:]) / d_period
                k_final = k_smoothed[-1]
                return k_final, d
        
        return 50, 50

    def calcular_regresion_lineal(self, x, y):
        if len(x) != len(y) or len(x) == 0:
            return None
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        denom = (n * sum_x2 - sum_x * sum_x)
        if denom == 0:
            pendiente = 0
        else:
            pendiente = (n * sum_xy - sum_x * sum_y) / denom
        intercepto = (sum_y - pendiente * sum_x) / n if n else 0
        return pendiente, intercepto

    def calcular_pearson_y