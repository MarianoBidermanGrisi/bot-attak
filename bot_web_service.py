# bot_web_service.py
import requests
import time
import json
import os
import sys
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
import threading  # Asegúrate de que threading esté importado
import logging
from telegram import Bot
from telegram.error import TelegramError

# Configurar logging para que se muestre en stdout/stderr
# Esto es crucial para que los logs aparezcan en Application Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout  # Forzar salida a stdout
)
logger = logging.getLogger(__name__)

# También configurar un logger para errores específicos
error_logger = logging.getLogger('error_logger')
error_handler = logging.StreamHandler(sys.stderr)  # Enviar errores a stderr
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
error_logger.addHandler(error_handler)
error_logger.setLevel(logging.ERROR)

# Agregar un logger para depuración
debug_logger = logging.getLogger('debug_logger')
debug_handler = logging.StreamHandler(sys.stdout)
debug_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
debug_logger.addHandler(debug_handler)
debug_logger.setLevel(logging.DEBUG)

# ---------------------------
# Optimizador IA (Mejorado)
# ---------------------------
class OptimizadorIA:
    def __init__(self, log_path="operaciones_log.csv", min_samples=15):
        logger.info("Inicializando OptimizadorIA")
        self.log_path = log_path
        self.min_samples = min_samples
        self.datos = self.cargar_datos()
        logger.info(f"OptimizadorIA inicializado con {len(self.datos)} datos")

    def cargar_datos(self):
        logger.info(f"Cargando datos desde {self.log_path}")
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
                    except Exception as e:
                        error_logger.error(f"Error procesando fila: {e}")
                        continue
            logger.info(f"Se cargaron {len(datos)} registros exitosamente")
        except FileNotFoundError:
            logger.warning(f"⚠ No se encontró {self.log_path} (optimizador)")
        except Exception as e:
            error_logger.error(f"Error cargando datos: {e}")
        return datos

    def evaluar_configuracion(self, trend_threshold, min_strength, entry_margin):
        debug_logger.debug(f"Evaluando configuración: trend={trend_threshold}, strength={min_strength}, margin={entry_margin}")
        if not self.datos:
            logger.warning("No hay datos para evaluar configuración")
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
            debug_logger.debug(f"Configuración rechazada: solo {n} operaciones filtradas")
            return -10000 - n
            
        pnls = [op['pnl'] for op in filtradas]
        pnl_mean = statistics.mean(pnls) if filtradas else 0
        pnl_std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        
        winrate = sum(1 for op in filtradas if op['pnl'] > 0) / n if n > 0 else 0
        
        score = (pnl_mean - 0.5 * pnl_std) * winrate * math.sqrt(n)
        
        ops_calidad = [op for op in filtradas if op.get('r2', 0) >= 0.6 and op.get('nivel_fuerza', 1) >= 3]
        if ops_calidad:
            score *= 1.2
            
        debug_logger.debug(f"Configuración evaluada: score={score}, n={n}")
        return score

    def buscar_mejores_parametros(self):
        logger.info("Iniciando búsqueda de mejores parámetros")
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
                logger.info("Parámetros guardados en mejores_parametros.json")
            except Exception as e:
                error_logger.error(f"⚠ Error guardando mejores_parametros.json: {e}")
        else:
            logger.warning("⚠ No se encontró una configuración mejor")
            
        return mejores_param

# ---------------------------
# BOT PRINCIPAL (MEJORADO)
# ---------------------------
class TradingBot:
    def __init__(self, auto_optimize=False, log_path="operaciones_log.csv"):
        logger.info("Inicializando TradingBot")
        self.log_path = log_path
        self.auto_optimize = auto_optimize
        
        # Obtener configuración de variables de entorno
        self.telegram_token = os.environ.get('TELEGRAM_TOKEN')
        telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_IDS', '')
        self.telegram_chat_ids = [chat_id.strip() for chat_id in telegram_chat_ids_str.split(',') if chat_id.strip()]
        
        logger.info(f"Token Telegram configurado: {'Sí' if self.telegram_token else 'No'}")
        logger.info(f"Chat IDs configurados: {len(self.telegram_chat_ids)}")
        
        # Inicializar bot de Telegram
        self.telegram_bot = None
        if self.telegram_token:
            try:
                self.telegram_bot = Bot(token=self.telegram_token)
                logger.info("Bot de Telegram inicializado correctamente")
            except Exception as e:
                error_logger.error(f"Error inicializando bot de Telegram: {e}")
        
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
        logger.info(f"Configuración cargada con {len(self.config['symbols'])} símbolos")
        
        # Guardar la configuración aplicada
        try:
            with open("parametros_predefinidos.json", "w", encoding='utf-8') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Configuración guardada en parametros_predefinidos.json")
        except Exception as e:
            error_logger.error(f"Error guardando configuración: {e}")

        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()
        logger.info("TradingBot inicializado completamente")

    def reoptimizar_periodicamente(self):
        logger.info("Verificando si se necesita reoptimización")
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
                else:
                    logger.warning("No se encontraron nuevos parámetros óptimos")
                    
        except Exception as e:
            error_logger.error(f"⚠ Error en re-optimización automática: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        logger.info(f"Actualizando parámetros: {nuevos_parametros}")
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', self.config['trend_threshold_degrees'])
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', self.config['min_trend_strength_degrees'])
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', self.config['entry_margin'])
        
        try:
            with open("parametros_actualizados.json", "w", encoding='utf-8') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Parámetros actualizados guardados en parametros_actualizados.json")
        except Exception as e:
            error_logger.error(f"Error guardando parámetros actualizados: {e}")

    async def _enviar_telegram_async(self, mensaje, chat_id):
        """Envía mensaje usando python-telegram-bot de forma asíncrona"""
        debug_logger.debug(f"Enviando mensaje a Telegram (chat_id={chat_id})")
        if not self.telegram_bot:
            logger.warning("Bot de Telegram no inicializado")
            return False
        
        try:
            await self.telegram_bot.send_message(
                chat_id=chat_id,
                text=mensaje,
                parse_mode='HTML'
            )
            logger.info(f"Mensaje enviado exitosamente a chat_id={chat_id}")
            return True
        except TelegramError as e:
            error_logger.error(f"Error enviando mensaje a Telegram: {e}")
            return False
        except Exception as e:
            error_logger.error(f"Error inesperado enviando a Telegram: {e}")
            return False

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Método de respaldo usando requests"""
        debug_logger.debug(f"Usando método de respaldo para enviar mensaje a {len(chat_ids)} chats")
        if not token or not chat_ids:
            logger.warning("No hay token o chat IDs configurados para Telegram")
            return False
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    logger.info(f"Mensaje enviado exitosamente a chat_id={chat_id}")
                    resultados.append(True)
                else:
                    error_logger.error(f"Error enviando mensaje a chat_id={chat_id}: {r.status_code} - {r.text}")
                    resultados.append(False)
            except Exception as e:
                error_logger.error(f"Excepción enviando mensaje a chat_id={chat_id}: {e}")
                resultados.append(False)
        return any(resultados)

    def enviar_telegram(self, mensaje):
        """Envía mensaje a todos los chats configurados"""
        debug_logger.debug("Preparando envío de mensaje a Telegram")
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
                if resultado:
                    logger.info("Mensaje enviado con python-telegram-bot")
                    return True
            except Exception as e:
                error_logger.error(f"Error con python-telegram-bot, usando método de respaldo: {e}")
        
        # Método de respaldo con requests
        resultado = self._enviar_telegram_simple(mensaje, self.telegram_token, self.telegram_chat_ids)
        if resultado:
            logger.info("Mensaje enviado con método de respaldo (requests)")
        return resultado

    def inicializar_log(self):
        logger.info(f"Inicializando log de operaciones en {self.archivo_log}")
        try:
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
                logger.info("Archivo de log creado con encabezados")
            else:
                logger.info("Archivo de log ya existe")
        except Exception as e:
            error_logger.error(f"Error inicializando log: {e}")

    def registrar_operacion(self, datos_operacion):
        debug_logger.debug(f"Registrando operación: {datos_operacion.get('symbol', 'Unknown')}")
        try:
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
            logger.info(f"Operación registrada: {datos_operacion['symbol']} {datos_operacion['tipo']} {datos_operacion['resultado']}")
        except Exception as e:
            error_logger.error(f"Error registrando operación: {e}")

    def verificar_cierre_operaciones(self):
        debug_logger.debug("Verificando cierre de operaciones activas")
        if not self.operaciones_activas:
            logger.debug("No hay operaciones activas para verificar")
            return []
        operaciones_cerradas = []
        logger.info(f"Verificando {len(self.operaciones_activas)} operaciones activas")
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            debug_logger.debug(f"Verificando operación para {simbolo}")
            datos = self.obtener_datos_mercado(simbolo)
            if not datos:
                logger.warning(f"No se pudieron obtener datos para {simbolo}")
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
                logger.info(f"Cierre detectado para {simbolo}: {resultado}")
                if tipo == 'LONG':
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
                
        if operaciones_cerradas:
            logger.info(f"Cerradas {len(operaciones_cerradas)} operaciones: {', '.join(operaciones_cerradas)}")
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
        debug_logger.debug(f"Obteniendo datos de mercado para {simbolo}")
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': simbolo, 'interval': self.config['interval'], 'limit': self.config['candle_period'] + 14}
        try:
            respuesta = requests.get(url, params=params, timeout=10)
            if respuesta.status_code != 200:
                error_logger.error(f"Error en API Binance para {simbolo}: {respuesta.status_code}")
                return None
                
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                error_logger.error(f"Respuesta inválida de Binance para {simbolo}")
                return None
                
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
            debug_logger.debug(f"Datos obtenidos para {simbolo}: precio_actual={cierres[-1] if cierres else 0}")
            return self.ultimos_datos[simbolo]
        except Exception as e:
            error_logger.error(f"❌ Error obteniendo {simbolo}: {e}")
            return None

    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        """Calcula el indicador Stochástico"""
        debug_logger.debug("Calculando indicador Stochástico")
        if len(datos_mercado['cierres']) < period:
            logger.warning("Datos insuficientes para calcular Stochástico")
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
                debug_logger.debug(f"Stochástico calculado: K={k_final:.2f}, D={d:.2f}")
                return k_final, d
        
        logger.warning("Error en cálculo de Stochástico, usando valores por defecto")
        return 50, 50

    def calcular_regresion_lineal(self, x, y):
        debug_logger.debug("Calculando regresión lineal")
        if len(x) != len(y) or len(x) == 0:
            logger.warning("Datos inválidos para regresión lineal")
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
        debug_logger.debug(f"Regresión lineal: pendiente={pendiente:.6f}, intercepto={intercepto:.6f}")
        return pendiente, intercepto

    def calcular_pearson_y_angulo(self, x, y):
        debug_logger.debug("Calculando Pearson y ángulo")
        if len(x) != len(y) or len(x) < 2:
            logger.warning("Datos insuficientes para calcular Pearson y ángulo")
            return 0, 0
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        sum_y2 = np.sum(y * y)
        numerator = n * sum_xy - sum_x * sum_y
        denominator = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
        if denominator == 0:
            logger.warning("Denominador cero en cálculo de Pearson")
            return 0, 0
        pearson = numerator / denominator
        denom_pend = (n * sum_x2 - sum_x * sum_x)
        pendiente = (n * sum_xy - sum_x * sum_y) / denom_pend if denom_pend != 0 else 0
        angulo_radianes = math.atan(pendiente * len(x) / (max(y) - min(y)) if (max(y) - min(y)) != 0 else 0)
        angulo_grados = math.degrees(angulo_radianes)
        debug_logger.debug(f"Pearson={pearson:.4f}, ángulo={angulo_grados:.2f}°")
        return pearson, angulo_grados

    def clasificar_fuerza_tendencia(self, angulo_grados):
        angulo_abs = abs(angulo_grados)
        if angulo_abs < 3:
            return "💔 Muy Débil", 1
        elif angulo_abs < 13:
            return "❤️‍🩹 Débil", 2
        elif angulo_abs < 27:
            return "💛 Moderada", 3
        elif angulo_abs < 45:
            return "💚 Fuerte", 4
        else:
            return "💙 Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        if abs(angulo_grados) < umbral_minimo:
            return "⚪ RANGO"
        elif angulo_grados > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_canal_regresion(self, datos_mercado):
        debug_logger.debug("Calculando canal de regresión")
        if not datos_mercado or len(datos_mercado['maximos']) < self.config['candle_period']:
            logger.warning("Datos insuficientes para calcular canal de regresión")
            return None
        start_idx = -self.config['candle_period']
        tiempos = datos_mercado['tiempos'][start_idx:]
        maximos = datos_mercado['maximos'][start_idx:]
        minimos = datos_mercado['minimos'][start_idx:]
        cierres = datos_mercado['cierres'][start_idx:]
        tiempos_reg = list(range(len(tiempos)))
        reg_max = self.calcular_regresion_lineal(tiempos_reg, maximos)
        reg_min = self.calcular_regresion_lineal(tiempos_reg, minimos)
        reg_close = self.calcular_regresion_lineal(tiempos_reg, cierres)
        if not all([reg_max, reg_min, reg_close]):
            logger.warning("Error en cálculo de regresiones para canal")
            return None
        pendiente_max, intercepto_max = reg_max
        pendiente_min, intercepto_min = reg_min
        pendiente_cierre, intercepto_cierre = reg_close
        tiempo_actual = tiempos_reg[-1]
        resistencia_media = pendiente_max * tiempo_actual + intercepto_max
        soporte_media = pendiente_min * tiempo_actual + intercepto_min
        tendencia_actual = pendiente_cierre * tiempo_actual + intercepto_cierre
        diferencias_max = [maximos[i] - (pendiente_max * tiempos_reg[i] + intercepto_max) for i in range(len(tiempos_reg))]
        diferencias_min = [minimos[i] - (pendiente_min * tiempos_reg[i] + intercepto_min) for i in range(len(tiempos_reg))]
        desviacion_max = np.std(diferencias_max) if diferencias_max else 0
        desviacion_min = np.std(diferencias_min) if diferencias_min else 0
        resistencia_superior = resistencia_media + desviacion_max
        soporte_inferior = soporte_media - desviacion_min
        precio_actual = datos_mercado['precio_actual']
        extension_velas = 3
        tiempos_futuros = list(range(tiempo_actual + 1, tiempo_actual + 1 + extension_velas))
        resistencia_futura = [pendiente_max * t + intercepto_max + desviacion_max for t in tiempos_futuros]
        soporte_futuro = [pendiente_min * t + intercepto_min - desviacion_min for t in tiempos_futuros]
        tendencia_futura = [pendiente_cierre * t + intercepto_cierre for t in tiempos_futuros]
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        
        rango_reciente = max(maximos[-5:]) - min(minimos[-5:]) if len(maximos) >= 5 else 0
        
        # Calcular Stochástico
        stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
        
        resultado = {
            'resistencia': resistencia_superior,
            'soporte': soporte_inferior,
            'resistencia_media': resistencia_media,
            'soporte_media': soporte_media,
            'linea_tendencia': tendencia_actual,
            'pendiente_tendencia': pendiente_cierre,
            'precio_actual': precio_actual,
            'ancho_canal': resistencia_superior - soporte_inferior,
            'angulo_tendencia': angulo_tendencia,
            'coeficiente_pearson': pearson,
            'fuerza_texto': fuerza_texto,
            'nivel_fuerza': nivel_fuerza,
            'direccion': direccion,
            'r2_score': self.calcular_r2(cierres, tiempos_reg, pendiente_cierre,intercepto_cierre),
            'resistencia_extendida': resistencia_futura,
            'soporte_extendido': soporte_futuro,
            'linea_tendencia_extendida': tendencia_futura,
            'velas_extension': extension_velas,
            'pendiente_resistencia': pendiente_max,
            'pendiente_soporte': pendiente_min,
            'rango_velas_reciente': rango_reciente,
            'maximos': maximos,
            'minimos': minimos,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d
        }
        
        debug_logger.debug(f"Canal calculado: dirección={direccion}, ángulo={angulo_tendencia:.2f}°, fuerza={fuerza_texto}")
        return resultado

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        if len(y_real) != len(x):
            logger.warning("Longitudes diferentes en cálculo R²")
            return 0
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        if ss_tot == 0:
            logger.warning("Varianza cero en cálculo R²")
            return 0
        r2 = 1 - (ss_res / ss_tot)
        debug_logger.debug(f"R² calculado: {r2:.4f}")
        return r2
    
    def generar_grafico_profesional(self, simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion):
        debug_logger.debug(f"Generando gráfico para {simbolo}")
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': simbolo,
                'interval': self.config['interval'],
                'limit': self.config['candle_period']
            }
            respuesta = requests.get(url, params=params, timeout=10)
            if respuesta.status_code != 200:
                error_logger.error(f"Error en API Binance para gráfico {simbolo}: {respuesta.status_code}")
                return None
                
            klines = respuesta.json()
            
            df_data = []
            for kline in klines:
                df_data.append({
                    'Date': pd.to_datetime(kline[0], unit='ms'),
                    'Open': float(kline[1]),
                    'High': float(kline[2]),
                    'Low': float(kline[3]),
                    'Close': float(kline[4]),
                    'Volume': float(kline[5])
                })
            
            df = pd.DataFrame(df_data)
            df.set_index('Date', inplace=True)
            
            # Calcular líneas del canal de regresión
            tiempos_reg = list(range(len(df)))
            resistencia_values = []
            soporte_values = []
            media_values = []
            
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                sop = info_canal['pendiente_soporte'] * t + \
                     (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                med = info_canal['pendiente_tendencia'] * t + \
                     (info_canal['linea_tendencia'] - info_canal['pendiente_tendencia'] * tiempos_reg[-1])
                
                resistencia_values.append(resist)
                soporte_values.append(sop)
                media_values.append(med)
            
            df['Resistencia'] = resistencia_values
            df['Soporte'] = soporte_values
            df['Media'] = media_values
            
            # Calcular Estocástico para el gráfico inferior
            period = 14
            k_period = 3
            d_period = 3
            
            stoch_k_values = []
            
            for i in range(len(df)):
                if i < period - 1:
                    stoch_k_values.append(50)
                else:
                    highest_high = df['High'].iloc[i-period+1:i+1].max()
                    lowest_low = df['Low'].iloc[i-period+1:i+1].min()
                    
                    if highest_high == lowest_low:
                        k = 50
                    else:
                        k = 100 * (df['Close'].iloc[i] - lowest_low) / (highest_high - lowest_low)
                    stoch_k_values.append(k)
            
            # Suavizar %K
            k_smoothed = []
            for i in range(len(stoch_k_values)):
                if i < k_period - 1:
                    k_smoothed.append(stoch_k_values[i])
                else:
                    k_avg = sum(stoch_k_values[i-k_period+1:i+1]) / k_period
                    k_smoothed.append(k_avg)
            
            # Calcular %D
            stoch_d_values = []
            for i in range(len(k_smoothed)):
                if i < d_period - 1:
                    stoch_d_values.append(k_smoothed[i])
                else:
                    d = sum(k_smoothed[i-d_period+1:i+1]) / d_period
                    stoch_d_values.append(d)
            
            df['Stoch_K'] = k_smoothed
            df['Stoch_D'] = stoch_d_values
            
            # Preparar plots adicionales para el gráfico principal
            apds = [
                mpf.make_addplot(df['Resistencia'], color='#5444ff', linestyle='--', 
                               width=2, label='Resistencia', panel=0),
                mpf.make_addplot(df['Soporte'], color="#5444ff", linestyle='--', 
                               width=2, label='Soporte', panel=0),
                
            ]
            
            # Añadir líneas de entrada, TP y SL al gráfico principal
            if precio_entrada and tp and sl:
                entry_line = [precio_entrada] * len(df)
                tp_line = [tp] * len(df)
                sl_line = [sl] * len(df)
                
                apds.append(mpf.make_addplot(entry_line, color='#FFD700', linestyle='-', 
                                           width=2, label='Entrada', panel=0))
                apds.append(mpf.make_addplot(tp_line, color='#00FF00', linestyle='-', 
                                           width=2, label='TP', panel=0))
                apds.append(mpf.make_addplot(sl_line, color='#FF0000', linestyle='-', 
                                           width=2, label='SL', panel=0))
            
            # Añadir Estocástico al panel inferior
            apds.append(mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, 
                                         label='%K', panel=1, ylabel='Estocástico'))
            apds.append(mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, 
                                         label='%D', panel=1))
            
            # Líneas de sobrecompra y sobreventa en el estocástico
            overbought = [80] * len(df)
            oversold = [20] * len(df)
            middle = [50] * len(df)
            
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', 
                                         width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', 
                                         width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(middle, color="#E4E2E2", linestyle=':', 
                                         width=0.6, panel=1, alpha=0.3))
            
            # Crear el gráfico con dos paneles
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} | Ángulo: {info_canal["angulo_tendencia"]:.1f}° | Stoch: {info_canal["stoch_k"]:.1f}/{info_canal["stoch_d"]:.1f}',
                               ylabel='Precio (USDT)',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 10),
                               panel_ratios=(3, 1))
            
            # Ajustar límites del panel del estocástico
            axes[2].set_ylim([0, 100])
            axes[2].set_ylabel('Estocástico', fontsize=10)
            axes[2].grid(True, alpha=0.3)
            
            # Añadir anotaciones de texto para los niveles
            if precio_entrada and tp and sl:
                axes[0].text(len(df)-1, precio_entrada, f' Entrada: {precio_entrada:.8f}', 
                            va='center', ha='left', color='#FFD700', fontsize=9, 
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
                axes[0].text(len(df)-1, tp, f' TP: {tp:.8f}', 
                            va='center', ha='left', color='#00FF00', fontsize=9,
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
                axes[0].text(len(df)-1, sl, f' SL: {sl:.8f}', 
                            va='center', ha='left', color='#FF0000', fontsize=9,
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            
            logger.info(f"Gráfico generado exitosamente para {simbolo}")
            return buf
        except Exception as e:
            error_logger.error(f"⚠️ Error generando gráfico {simbolo}: {e}")
            return None

    async def _enviar_grafico_async(self, buf, chat_id):
        """Envía gráfico usando python-telegram-bot de forma asíncrona"""
        debug_logger.debug(f"Enviando gráfico a chat_id={chat_id}")
        if not self.telegram_bot:
            logger.warning("Bot de Telegram no inicializado para enviar gráfico")
            return False
        
        try:
            buf.seek(0)
            await self.telegram_bot.send_photo(
                chat_id=chat_id,
                photo=buf,
                caption="Gráfico de análisis técnico"
            )
            logger.info(f"Gráfico enviado exitosamente a chat_id={chat_id}")
            return True
        except TelegramError as e:
            error_logger.error(f"Error enviando gráfico a Telegram: {e}")
            return False
        except Exception as e:
            error_logger.error(f"Error inesperado enviando gráfico a Telegram: {e}")
            return False

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía gráfico a todos los chats configurados"""
        debug_logger.debug(f"Preparando envío de gráfico a {len(chat_ids)} chats")
        if not buf or not token or not chat_ids:
            logger.warning("Parámetros inválidos para enviar gráfico")
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
                        resultado = await self._enviar_grafico_async(buf, chat_id)
                        resultados.append(resultado)
                    return any(resultados)
                
                resultado = loop.run_until_complete(enviar_a_todos())
                loop.close()
                if resultado:
                    logger.info("Gráfico enviado con python-telegram-bot")
                    return True
            except Exception as e:
                error_logger.error(f"Error con python-telegram-bot, usando método de respaldo: {e}")
        
        # Método de respaldo con requests
        exito = False
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            try:
                buf.seek(0)
                files = {'photo': ('grafico.png', buf.read(), 'image/png')}
                data = {'chat_id': chat_id}
                
                r = requests.post(url, files=files, data=data, timeout=30)
                
                if r.status_code == 200:
                    logger.info(f"     ✅ Gráfico enviado correctamente a chat {chat_id}")
                    exito = True
                else:
                    error_logger.error(f"     ⚠️ Error enviando gráfico a {chat_id}: HTTP {r.status_code}")
                    
            except Exception as e:
                error_logger.error(f"     ❌ Excepción enviando gráfico a {chat_id}: {e}")
                
        return exito

    def detectar_touch_canal(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio está TOCANDO el canal (no solo acercándose)"""
        debug_logger.debug(f"Detectando toque de canal para {simbolo}")
        if not info_canal:
            logger.warning(f"No hay información de canal para {simbolo}")
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        angulo = info_canal['angulo_tendencia']
        direccion = info_canal['direccion']
        nivel_fuerza = info_canal['nivel_fuerza']
        r2 = info_canal['r2_score']
        pearson = info_canal['coeficiente_pearson']
        ancho_canal = info_canal['ancho_canal']
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        precio_medio = (resistencia + soporte) / 2
        
        # Verificar si el canal es válido
        if ancho_canal / precio_medio < self.config['min_channel_width']:
            debug_logger.debug(f"Canal demasiado estrecho para {simbolo}: {ancho_canal/precio_medio:.6f}")
            return None
        
        # Verificar fuerza mínima de tendencia
        if abs(angulo) < self.config['min_trend_strength_degrees']:
            debug_logger.debug(f"Tendencia demasiado débil para {simbolo}: {abs(angulo):.2f}°")
            return None
        
        # Verificar calidad del canal
        if abs(pearson) < 0.4 or r2 < 0.4:
            debug_logger.debug(f"Canal de baja calidad para {simbolo}: pearson={abs(pearson):.3f}, r2={r2:.3f}")
            return None
        
        # Calcular tolerancia para "tocar" el canal (muy pequeña)
        tolerancia = 0.0005 * precio_medio  # 0.05% de tolerancia
        
        # Detectar TOQUE en SOPORTE con Stochástico OVERSOLD para LONG
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            distancia_soporte = abs(precio_actual - soporte)
            if distancia_soporte <= tolerancia:
                # Verificar Stochástico en sobreventa
                if stoch_k <= 25 and stoch_d <= 30:
                    logger.info(f"Señal LONG detectada para {simbolo}: toque en soporte con Stochástico oversold")
                    return "LONG"
        
        # Detectar TOQUE en RESISTENCIA con Stochástico OVERBOUGHT para SHORT
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            distancia_resistencia = abs(precio_actual - resistencia)
            if distancia_resistencia <= tolerancia:
                # Verificar Stochástico en sobrecompra
                if stoch_k >= 75 and stoch_d >= 70:
                    logger.info(f"Señal SHORT detectada para {simbolo}: toque en resistencia con Stochástico overbought")
                    return "SHORT"
                    
        return None

    def generar_mensaje_senal(self, simbolo, tipo_operacion, info_canal, precio_entrada, tp, sl):
        emoji = "🟢" if tipo_operacion == "LONG" else "🔴"
        direccion = "ALCISTA" if tipo_operacion == "LONG" else "BAJISTA"
        rr_ratio = abs(tp - precio_entrada) / abs(sl - precio_entrada) if sl != precio_entrada else 0
        
        mensaje = f"""
{emoji} <b>SEÑAL DE TRADING DETECTADA</b>

📊 <b>{simbolo}</b> - {direccion}
💰 <b>Precio Entrada:</b> {precio_entrada:.8f}
🎯 <b>Take Profit:</b> {tp:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f}

📈 <b>Riesgo/Beneficio:</b> 1:{rr_ratio:.2f}
📐 <b>Ángulo Tendencia:</b> {info_canal['angulo_tendencia']:.1f}°
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📊 <b>Pearson:</b> {info_canal['coeficiente_pearson']:.3f}
🎯 <b>R²:</b> {info_canal['r2_score']:.3f}
📊 <b>Stoch K/D:</b> {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f}

🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC
        """
        return mensaje

    ### NUEVO ###
    def escanear_y_enviar_senales(self):
        """Función principal que escanea todos los símbolos y envía señales si se detectan."""
        logger.info("🔍 Iniciando escaneo del mercado...")
        for simbolo in self.config['symbols']:
            if simbolo in self.operaciones_activas or simbolo in self.senales_enviadas:
                continue
            
            datos_mercado = self.obtener_datos_mercado(simbolo)
            if not datos_mercado:
                continue
            
            info_canal = self.calcular_canal_regresion(datos_mercado)
            if not info_canal:
                continue
            
            tipo_operacion = self.detectar_touch_canal(simbolo, info_canal, datos_mercado)
            if tipo_operacion:
                precio_entrada = datos_mercado['precio_actual']
                ancho_canal = info_canal['ancho_canal']
                
                if tipo_operacion == "LONG":
                    tp = precio_entrada + (ancho_canal * self.config['min_rr_ratio'])
                    sl = precio_entrada - (ancho_canal * self.config['entry_margin'])
                else: # SHORT
                    tp = precio_entrada - (ancho_canal * self.config['min_rr_ratio'])
                    sl = precio_entrada + (ancho_canal * self.config['entry_margin'])
                
                # Validar R/R ratio
                rr_ratio = abs(tp - precio_entrada) / abs(sl - precio_entrada)
                if rr_ratio < self.config['min_rr_ratio']:
                    logger.info(f"Señal para {simbolo} descartada: R/R ratio ({rr_ratio:.2f}) inferior al mínimo ({self.config['min_rr_ratio']})")
                    continue
                
                # Enviar señal
                mensaje = self.generar_mensaje_senal(simbolo, tipo_operacion, info_canal, precio_entrada, tp, sl)
                self.enviar_telegram(mensaje)
                
                # Generar y enviar gráfico
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    self.enviar_grafico_telegram(buf, self.telegram_token, self.telegram_chat_ids)
                
                # Registrar operación activa
                self.operaciones_activas[simbolo] = {
                    'tipo': tipo_operacion,
                    'precio_entrada': precio_entrada,
                    'take_profit': tp,
                    'stop_loss': sl,
                    'timestamp_entrada': datetime.now().isoformat(),
                    'angulo_tendencia': info_canal['angulo_tendencia'],
                    'pearson': info_canal['coeficiente_pearson'],
                    'r2_score': info_canal['r2_score'],
                    'ancho_canal_relativo': ancho_canal / precio_entrada,
                    'nivel_fuerza': info_canal['nivel_fuerza'],
                    'rango_velas_entrada': info_canal['rango_velas_reciente'],
                    'stoch_k': info_canal['stoch_k'],
                    'stoch_d': info_canal['stoch_d']
                }
                self.senales_enviadas.add(simbolo)
                logger.info(f"✅ Señal enviada para {simbolo} y operación activa registrada.")
        
        logger.info("🏁 Escaneo del mercado finalizado.")


# ---------------------------
# APLICACIÓN FLASK Y CONFIGURACIÓN
# ---------------------------

app = Flask(__name__)

### CAMBIO ###
# Instanciamos el bot de trading una sola vez a nivel global
# Esto asegura que el estado del bot (operaciones activas, etc.) se mantenga entre peticiones
bot = TradingBot(auto_optimize=True)

### NUEVO ###
# Función que se ejecutará en un hilo separado para no bloquear el servidor web
def run_bot_loop():
    logger.info("🚀 Hilo del bot de trading iniciado.")
    while True:
        try:
            # 1. Verificar si alguna operación activa debe cerrarse
            bot.verificar_cierre_operaciones()
            
            # 2. Escanear el mercado en busca de nuevas señales
            bot.escanear_y_enviar_senales()
            
            # 3. Esperar el intervalo configurado antes del siguiente escaneo
            time.sleep(bot.config['scan_interval_minutes'] * 60)
            
        except Exception as e:
            error_logger.error(f"Ocurrió un error en el hilo principal del bot: {e}")
            # En caso de error, esperar un minuto antes de reintentar para evitar bucles de error rápidos
            time.sleep(60)

### NUEVO ###
# Iniciar el hilo del bot cuando la aplicación se carga
# daemon=True asegura que el hilo se cerrará cuando la aplicación principal se detenga
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()
logger.info("✅ Hilo del bot iniciado en segundo plano.")


### CAMBIO ###
# Ruta principal para health checks (usada por Render)
@app.route('/')
def index():
    return "Bot de Trading está en línea y operativo.", 200

### NUEVO ###
# Ruta del webhook para recibir actualizaciones de Telegram
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"✅ Update recibido de Telegram: {json.dumps(update, indent=2)}")
        
        # Aquí puedes procesar los mensajes, como comandos /start, /status, etc.
        # Por ahora, solo lo registramos y confirmamos la recepción a Telegram.
        # Ejemplo:
        # if 'message' in update and '/status' in update['message']['text']:
        #     bot.enviar_telegram("El bot está funcionando correctamente!")
        
        return jsonify({"status": "ok"}), 200
    else:
        logger.warning("⚠️ Petición recibida en /webhook no es JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

# Este bloque es para desarrollo local y será ignorado por Gunicorn en Render
if __name__ == '__main__':
    app.run(port=5000, debug=True)
