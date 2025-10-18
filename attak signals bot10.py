from flask import Flask, render_template, request, jsonify, session
import threading
import json
import os
from datetime import datetime
import time
import requests
import numpy as np
import math
import csv
import itertools
import statistics
import random
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tu-clave-secreta-super-segura-12345')

# Variables globales para el estado del bot
bot_instance = None
bot_thread = None
bot_running = False
bot_config = None

# Cargar configuración persistente
CONFIG_FILE = 'bot_config.json'

def cargar_configuracion():
    """Carga la configuración guardada"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return None

def guardar_configuracion(config):
    """Guarda la configuración"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error guardando configuración: {e}")
        return False

# ==================== CLASES DEL BOT ====================

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
            print("⚠ No se encontró operaciones_log.csv")
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
            print(f"ℹ️ No hay suficientes datos para optimizar")
            return None
            
        mejor_score = -1e9
        mejores_param = None
        
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003]
        
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        print(f"🔎 Optimizador: probando {total} combinaciones...")
        
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if score > mejor_score:
                mejor_score = score
                mejores_param = {
                    'trend_threshold_degrees': t,
                    'min_trend_strength_degrees': s,
                    'entry_margin': m,
                    'score': score
                }
                
        if mejores_param:
            print("✅ Optimizador: mejores parámetros encontrados:", mejores_param)
        
        return mejores_param


class TradingBot:
    def __init__(self, config_dict):
        self.config = config_dict
        self.log_path = "operaciones_log.csv"
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.total_operaciones = 0
        self.breakout_history = {}
        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.running = True
        
        self.inicializar_log()
        
        # Auto-optimización si está habilitada
        if self.config.get('auto_optimize', False):
            self.aplicar_optimizacion()

    def aplicar_optimizacion(self):
        try:
            ia = OptimizadorIA(log_path=self.log_path, min_samples=15)
            parametros_optimizados = ia.buscar_mejores_parametros()
            
            if parametros_optimizados:
                print("🔧 Aplicando parámetros optimizados...")
                self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 13)
                self.config['entry_margin'] = parametros_optimizados.get('entry_margin', 0.001)
                self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 16)
        except Exception as e:
            print(f"⚠️ Error en optimización: {e}")

    def inicializar_log(self):
        if not os.path.exists(self.log_path):
            with open(self.log_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'tipo', 'precio_entrada',
                    'take_profit', 'stop_loss', 'precio_salida',
                    'resultado', 'pnl_percent', 'duracion_minutos',
                    'angulo_tendencia', 'pearson', 'r2_score',
                    'ancho_canal_relativo', 'nivel_fuerza',
                    'rango_velas_entrada', 'stoch_k', 'stoch_d'
                ])

    def registrar_operacion(self, datos_operacion):
        with open(self.log_path, 'a', newline='', encoding='utf-8') as f:
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

    def obtener_datos_mercado(self, simbolo):
        url = "https://api.binance.com/api/v3/klines"
        params = {
            'symbol': simbolo,
            'interval': self.config['interval'],
            'limit': self.config['candle_period'] + 14
        }
        try:
            respuesta = requests.get(url, params=params, timeout=10)
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                raise ValueError("Respuesta inválida")
            
            maximos = [float(vela[2]) for vela in datos]
            minimos = [float(vela[3]) for vela in datos]
            cierres = [float(vela[4]) for vela in datos]
            tiempos = list(range(len(datos)))
            
            self.ultimos_datos[simbolo] = {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'tiempos': tiempos,
                'precio_actual': cierres[-1] if cierres else 0
            }
            return self.ultimos_datos[simbolo]
        except Exception as e:
            print(f"❌ Error obteniendo {simbolo}: {e}")
            return None

    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        if len(datos_mercado['cierres']) < period:
            return 50, 50
            
        cierres = datos_mercado['cierres']
        maximos = datos_mercado['maximos']
        minimos = datos_mercado['minimos']
        
        k_values = []
        for i in range(period-1, len(cierres)):
            highest_high = max(maximos[i-period+1:i+1])
            lowest_low = min(minimos[i-period+1:i+1])
            if highest_high == lowest_low:
                k = 50
            else:
                k = 100 * (cierres[i] - lowest_low) / (highest_high - lowest_low)
            k_values.append(k)
        
        if len(k_values) >= k_period:
            k_smoothed = []
            for i in range(k_period-1, len(k_values)):
                k_avg = sum(k_values[i-k_period+1:i+1]) / k_period
                k_smoothed.append(k_avg)
            
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

    def calcular_pearson_y_angulo(self, x, y):
        if len(x) != len(y) or len(x) < 2:
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
            return 0, 0
        pearson = numerator / denominator
        denom_pend = (n * sum_x2 - sum_x * sum_x)
        pendiente = (n * sum_xy - sum_x * sum_y) / denom_pend if denom_pend != 0 else 0
        angulo_radianes = math.atan(pendiente * len(x) / (max(y) - min(y)) if (max(y) - min(y)) != 0 else 0)
        angulo_grados = math.degrees(angulo_radianes)
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

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        if len(y_real) != len(x):
            return 0
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        if ss_tot == 0:
            return 0
        return 1 - (ss_res / ss_tot)

    def calcular_canal_regresion(self, datos_mercado):
        if not datos_mercado or len(datos_mercado['maximos']) < self.config['candle_period']:
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
        
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        
        rango_reciente = max(maximos[-5:]) - min(minimos[-5:]) if len(maximos) >= 5 else 0
        stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
        
        return {
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
            'r2_score': self.calcular_r2(cierres, tiempos_reg, pendiente_cierre, intercepto_cierre),
            'pendiente_resistencia': pendiente_max,
            'pendiente_soporte': pendiente_min,
            'rango_velas_reciente': rango_reciente,
            'maximos': maximos,
            'minimos': minimos,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d
        }

    def detectar_touch_canal(self, simbolo, info_canal, datos_mercado):
        if not info_canal:
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
        
        if ancho_canal / precio_medio < self.config['min_channel_width']:
            return None
        
        if abs(angulo) < self.config['min_trend_strength_degrees']:
            return None
        
        if abs(pearson) < 0.4 or r2 < 0.4:
            return None
        
        tolerancia = 0.0005 * precio_medio
        
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            distancia_soporte = abs(precio_actual - soporte)
            if distancia_soporte <= tolerancia:
                if stoch_k <= 25 and stoch_d <= 30:
                    return "LONG"
        
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            distancia_resistencia = abs(precio_actual - resistencia)
            if distancia_resistencia <= tolerancia:
                if stoch_k >= 75 and stoch_d >= 70:
                    return "SHORT"
        
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = soporte - (ancho_canal * 0.3)
            take_profit = precio_entrada + (ancho_canal * 0.9)
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia + (ancho_canal * 0.3)
            take_profit = precio_entrada - (ancho_canal * 0.9)
        
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        if ratio_rr < self.config['min_rr_ratio']:
            if tipo_operacion == "LONG":
                take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
            else:
                take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
        
        return precio_entrada, take_profit, stop_loss

    def enviar_telegram(self, mensaje):
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if not token or not chat_ids:
            return False
        
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                requests.post(url, json=payload, timeout=10)
            except:
                pass
        return True

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, datos_mercado):
        if simbolo in self.senales_enviadas:
            return
        
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
        
        mensaje = f"""
🎯 <b>SEÑAL DE {tipo_operacion} - {simbolo}</b>

💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
🎯 <b>Entrada:</b> {precio_entrada:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f}
🎯 <b>Take Profit:</b> {tp:.8f}

📊 <b>Ratio R/B:</b> {ratio_rr:.2f}:1
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📐 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
🎰 <b>Stochástico:</b> {stoch_estado}
📊 <b>Stoch K:</b> {info_canal['stoch_k']:.1f}
📈 <b>Stoch D:</b> {info_canal['stoch_d']:.1f}

⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        self.enviar_telegram(mensaje)
        print(f"✅ Señal {tipo_operacion} para {simbolo}")
        
        self.operaciones_activas[simbolo] = {
            'tipo': tipo_operacion,
            'precio_entrada': precio_entrada,
            'take_profit': tp,
            'stop_loss': sl,
            'timestamp_entrada': datetime.now().isoformat(),
            'angulo_tendencia': info_canal['angulo_tendencia'],
            'pearson': info_canal['coeficiente_pearson'],
            'r2_score': info_canal['r2_score'],
            'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada,
            'nivel_fuerza': info_canal['nivel_fuerza'],
            'rango_velas_entrada': info_canal.get('rango_velas_reciente', 0),
            'stoch_k': info_canal['stoch_k'],
            'stoch_d': info_canal['stoch_d']
        }
        
        self.senales_enviadas.add(simbolo)
        self.total_operaciones += 1

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
                
                emoji = "🟢" if resultado == "TP" else "🔴"
                mensaje = f"{emoji} <b>OPERACIÓN CERRADA - {simbolo}</b>\n\n"
                mensaje += f"<b>RESULTADO: {resultado}</b>\n"
                mensaje += f"PnL %: {pnl_percent:.2f}%\n"
                mensaje += f"Duración: {duracion_minutos:.1f} min"
                
                self.enviar_telegram(mensaje)
                self.registrar_operacion(datos_operacion)
                operaciones_cerradas.append(simbolo)
                del self.operaciones_activas[simbolo]
                
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
                
                self.operaciones_desde_optimizacion += 1
                print(f"📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
        
        return operaciones_cerradas

    def ejecutar_ciclo(self):
        """Ejecuta un ciclo completo de análisis"""
        print(f"\n🔍 Escaneando {len(self.config['symbols'])} símbolos...")
        
        # Verificar cierres de operaciones
        cierres = self.verificar_cierre_operaciones()
        if cierres:
            print(f"📊 Operaciones cerradas: {', '.join(cierres)}")
        
        # Escanear símbolos
        for simbolo in self.config['symbols']:
            if not self.running:
                break
                
            if simbolo in self.operaciones_activas:
                continue
            
            datos = self.obtener_datos_mercado(simbolo)
            if not datos:
                continue
            
            canal = self.calcular_canal_regresion(datos)
            if not canal:
                continue
            
            # Verificar condiciones
            if (canal['nivel_fuerza'] < 2 or 
                abs(canal['coeficiente_pearson']) < 0.4 or 
                canal['r2_score'] < 0.4):
                continue
            
            tipo_operacion = self.detectar_touch_canal(simbolo, canal, datos)
            if tipo_operacion:
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, canal, datos['precio_actual']
                )
                if precio_entrada and tp and sl:
                    self.generar_senal_operacion(simbolo, tipo_operacion, precio_entrada, tp, sl, canal, datos)
            
            time.sleep(0.3)

    def run(self):
        """Loop principal del bot"""
        print("🤖 Bot iniciado")
        while self.running:
            try:
                self.ejecutar_ciclo()
                
                # Esperar intervalo configurado
                minutos = self.config['scan_interval_minutes']
                for _ in range(minutos * 60):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                print(f"❌ Error en ciclo: {e}")
                time.sleep(60)
        
        print("🛑 Bot detenido")

    def stop(self):
        """Detiene el bot"""
        self.running = False

    def get_status(self):
        """Retorna el estado actual del bot"""
        return {
            'running': self.running,
            'operaciones_activas': len(self.operaciones_activas),
            'total_operaciones': self.total_operaciones,
            'operaciones_activas_detalle': [
                {
                    'symbol': sym,
                    'tipo': op['tipo'],
                    'precio_entrada': op['precio_entrada'],
                    'take_profit': op['take_profit'],
                    'stop_loss': op['stop_loss']
                }
                for sym, op in self.operaciones_activas.items()
            ]
        }


# ==================== RUTAS FLASK ====================

@app.route('/')
def index():
    """Página principal"""
    config = cargar_configuracion()
    tiene_config = config is not None
    
    status = None
    if bot_instance:
        status = bot_instance.get_status()
    
    return render_template('index.html', 
                         tiene_config=tiene_config, 
                         bot_running=bot_running,
                         status=status)

@app.route('/config', methods=['GET', 'POST'])
def configurar():
    """Página de configuración"""
    if request.method == 'POST':
        try:
            # Procesar símbolos
            symbols_raw = request.form.get('symbols', '')
            symbols = [s.strip().upper() for s in symbols_raw.split(',') if s.strip()]
            
            # Procesar chat IDs
            chat_ids_raw = request.form.get('telegram_chat_ids', '')
            chat_ids = [c.strip() for c in chat_ids_raw.split(',') if c.strip()]
            
            config = {
                'candle_period': int(request.form.get('candle_period', 90)),
                'interval': request.form.get('interval', '15m'),
                'trend_threshold_degrees': float(request.form.get('trend_threshold_degrees', 13)),
                'min_trend_strength_degrees': float(request.form.get('min_trend_strength_degrees', 16)),
                'entry_margin': float(request.form.get('entry_margin', 0.001)),
                'min_channel_width': float(request.form.get('min_channel_width', 0.0002)),
                'min_rr_ratio': float(request.form.get('min_rr_ratio', 1.2)),
                'scan_interval_minutes': int(request.form.get('scan_interval_minutes', 1)),
                'auto_optimize': request.form.get('auto_optimize') == 'on',
                'symbols': symbols,
                'telegram_token': request.form.get('telegram_token', '').strip() or None,
                'telegram_chat_ids': chat_ids
            }
            
            if guardar_configuracion(config):
                return jsonify({'success': True, 'message': 'Configuración guardada correctamente'})
            else:
                return jsonify({'success': False, 'message': 'Error al guardar configuración'}), 500
                
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 400
    
    # GET - mostrar formulario
    config = cargar_configuracion()
    if not config:
        # Configuración por defecto
        config = {
            'candle_period': 90,
            'interval': '15m',
            'trend_threshold_degrees': 13,
            'min_trend_strength_degrees': 16,
            'entry_margin': 0.001,
            'min_channel_width': 0.0002,
            'min_rr_ratio': 1.2,
            'scan_interval_minutes': 1,
            'auto_optimize': True,
            'symbols': ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'DOGEUSDT'],
            'telegram_token': '',
            'telegram_chat_ids': []
        }
    
    return render_template('config.html', config=config)

@app.route('/api/start', methods=['POST'])
def start_bot():
    """Inicia el bot"""
    global bot_instance, bot_thread, bot_running
    
    if bot_running:
        return jsonify({'success': False, 'message': 'El bot ya está en ejecución'})
    
    config = cargar_configuracion()
    if not config:
        return jsonify({'success': False, 'message': 'No hay configuración guardada. Configure el bot primero.'}), 400
    
    try:
        bot_instance = TradingBot(config)
        bot_running = True
        
        bot_thread = threading.Thread(target=bot_instance.run, daemon=True)
        bot_thread.start()
        
        return jsonify({'success': True, 'message': 'Bot iniciado correctamente'})
    except Exception as e:
        bot_running = False
        return jsonify({'success': False, 'message': f'Error al iniciar bot: {str(e)}'}), 500

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """Detiene el bot"""
    global bot_instance, bot_running
    
    if not bot_running or not bot_instance:
        return jsonify({'success': False, 'message': 'El bot no está en ejecución'})
    
    try:
        bot_instance.stop()
        bot_running = False
        return jsonify({'success': True, 'message': 'Bot detenido correctamente'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error al detener bot: {str(e)}'}), 500

@app.route('/api/status')
def get_status():
    """Obtiene el estado actual del bot"""
    if not bot_instance:
        return jsonify({
            'running': False,
            'operaciones_activas': 0,
            'total_operaciones': 0,
            'operaciones_activas_detalle': []
        })
    
    return jsonify(bot_instance.get_status())

@app.route('/api/logs')
def get_logs():
    """Obtiene las últimas operaciones del log"""
    try:
        logs = []
        if os.path.exists('operaciones_log.csv'):
            with open('operaciones_log.csv', 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                logs = list(reader)[-50:]  # Últimas 50 operaciones
                logs.reverse()  # Más recientes primero
        
        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/health')
def health():
    """Health check para Render"""
    return jsonify({'status': 'ok', 'bot_running': bot_running})

# ==================== INICIO AUTOMÁTICO ====================

def iniciar_bot_automaticamente():
    """Inicia el bot automáticamente si hay configuración guardada"""
    global bot_instance, bot_thread, bot_running
    
    config = cargar_configuracion()
    if config:
        print("🔄 Configuración encontrada. Iniciando bot automáticamente...")
        try:
            bot_instance = TradingBot(config)
            bot_running = True
            
            bot_thread = threading.Thread(target=bot_instance.run, daemon=True)
            bot_thread.start()
            
            print("✅ Bot iniciado automáticamente")
        except Exception as e:
            print(f"❌ Error al iniciar bot automáticamente: {e}")
            bot_running = False

# Iniciar bot al cargar la aplicación
iniciar_bot_automaticamente()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
