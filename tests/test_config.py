#!/usr/bin/env python3
"""
Prueba de configuración de mínimos Bitget 2025
Verifica que las configuraciones funcionen correctamente
"""

import sys
import os

# Agregar el directorio raíz del proyecto al path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_bitget_config():
    """Prueba la configuración de Bitget"""
    print("=" * 60)
    print("🧪 PRUEBA DE CONFIGURACIÓN MÍNIMOS BITGET 2025")
    print("=" * 60)
    
    try:
        from config.bitget_config import get_minimum_size, get_recommended_leverage, get_price_precision
        print("✅ Configuración centralizada importada correctamente")
    except ImportError as e:
        print(f"❌ Error importando configuración: {e}")
        return False
    
    # Símbolos de prueba
    test_symbols = [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 
        'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'UNKNOWN'
    ]
    
    print("\n📊 RESULTADOS DE PRUEBAS:")
    print("-" * 60)
    
    all_passed = True
    
    for symbol in test_symbols:
        try:
            min_size = get_minimum_size(symbol)
            leverage = get_recommended_leverage(symbol)
            precision = get_price_precision(symbol)
            
            # Verificaciones
            passed = True
            issues = []
            
            if min_size <= 0:
                issues.append("Tamaño mínimo debe ser > 0")
                passed = False
            
            if leverage <= 0 or leverage > 20:
                issues.append("Apalancamiento debe estar entre 1-20")
                passed = False
                
            if precision < 0 or precision > 10:
                issues.append("Precisión debe estar entre 0-10")
                passed = False
            
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"{symbol:10} | Min: {min_size:>8} | Lev: {leverage:>2}x | Prec: {precision:>2} | {status}")
            
            if issues:
                for issue in issues:
                    print(f"            ⚠️  {issue}")
                all_passed = False
                
        except Exception as e:
            print(f"{symbol:10} | ERROR: {e}")
            all_passed = False
    
    print("-" * 60)
    
    # Prueba específica de los mínimos oficiales
    print("\n🎯 VERIFICACIÓN DE MÍNIMOS OFICIALES:")
    official_minimums = {
        'BTCUSDT': 0.001,
        'ETHUSDT': 0.01
    }
    
    for symbol, expected_min in official_minimums.items():
        actual_min = get_minimum_size(symbol)
        if actual_min == expected_min:
            print(f"✅ {symbol}: {actual_min} (correcto)")
        else:
            print(f"❌ {symbol}: {actual_min} (esperado: {expected_min})")
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 TODAS LAS PRUEBAS PASARON - Configuración correcta")
        return True
    else:
        print("⚠️ ALGUNAS PRUEBAS FALLARON - Revisar configuración")
        return False

def test_dynamic_sizing():
    """Prueba la función de detección automática de tamaño"""
    print("\n" + "=" * 60)
    print("🧪 PRUEBA DE DETECCIÓN AUTOMÁTICA DE TAMAÑO")
    print("=" * 60)
    
    try:
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from test_real_order import get_minimum_size_for_symbol
        print("✅ Función de detección automática importada")
    except ImportError as e:
        print(f"❌ Error importando función: {e}")
        return False
    
    test_cases = [
        ('BTCUSDT', '0.001'),
        ('ETHUSDT', '0.01'),
        ('BNBUSDT', '0.001'),
        ('UNKNOWN', '0.001')
    ]
    
    print("\n📊 RESULTADOS:")
    all_passed = True
    
    for symbol, expected in test_cases:
        try:
            result = get_minimum_size_for_symbol(symbol)
            if result == expected:
                print(f"✅ {symbol}: {result} (correcto)")
            else:
                print(f"❌ {symbol}: {result} (esperado: {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ {symbol}: ERROR - {e}")
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 DETECCIÓN AUTOMÁTICA FUNCIONANDO CORRECTAMENTE")
        return True
    else:
        print("⚠️ DETECCIÓN AUTOMÁTICA TIENE PROBLEMAS")
        return False

def main():
    """Función principal de pruebas"""
    print("🚀 INICIANDO PRUEBAS DE CONFIGURACIÓN BITGET")
    
    test1_passed = test_bitget_config()
    test2_passed = test_dynamic_sizing()
    
    print("\n" + "=" * 60)
    print("📋 RESUMEN FINAL:")
    print("=" * 60)
    print(f"Configuración Centralizada: {'✅ PASS' if test1_passed else '❌ FAIL'}")
    print(f"Detección Automática:      {'✅ PASS' if test2_passed else '❌ FAIL'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 TODAS LAS PRUEBAS PASARON")
        print("✅ La configuración está lista para usar en producción")
        return True
    else:
        print("\n⚠️ ALGUNAS PRUEBAS FALLARON")
        print("❌ Revisar configuración antes de usar en producción")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
