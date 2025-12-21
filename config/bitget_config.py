"""
Configuración de mínimos de trading para Bitget 2025
Basado en las reglas oficiales de Bitget para USDT-M Futures
"""

# Mínimos oficiales de Bitget para USDT-M Futures (actualizados 2025)
BITGET_MINIMUMS = {
    # Criptomonedas principales
    'BTCUSDT': 0.001,    # Bitcoin: 0.001 BTC
    'ETHUSDT': 0.01,     # Ethereum: 0.01 ETH
    'BNBUSDT': 0.01,     # Binance Coin: 0.01 BNB
    'ADAUSDT': 1.0,      # Cardano: 1 ADA
    'XRPUSDT': 1.0,      # Ripple: 1 XRP
    'SOLUSDT': 0.01,     # Solana: 0.01 SOL
    'DOGEUSDT': 1.0,     # Dogecoin: 1 DOGE
    'DOTUSDT': 0.1,      # Polkadot: 0.1 DOT
    'AVAXUSDT': 0.01,    # Avalanche: 0.01 AVAX
    'SHIBUSDT': 1000.0,  # Shiba Inu: 1000 SHIB
    
    # Altcoins principales
    'MATICUSDT': 1.0,    # Polygon: 1 MATIC
    'LTCUSDT': 0.01,     # Litecoin: 0.01 LTC
    'LINKUSDT': 0.1,     # Chainlink: 0.1 LINK
    'UNIUSDT': 0.1,      # Uniswap: 0.1 UNI
    'ATOMUSDT': 0.1,     # Cosmos: 0.1 ATOM
    'XLMUSDT': 1.0,      # Stellar: 1 XLM
    'VETUSDT': 1.0,      # VeChain: 1 VET
    'FILUSDT': 0.1,      # Filecoin: 0.1 FIL
    'TRXUSDT': 1.0,      # TRON: 1 TRX
    'ETCUSDT': 0.1,      # Ethereum Classic: 0.1 ETC
    
    # Por defecto para símbolos no listados específicamente
    'DEFAULT': 0.001
}

def get_minimum_size(symbol):
    """
    Obtiene el tamaño mínimo de orden para un símbolo específico
    Args:
        symbol (str): Símbolo del par de trading (ej: 'BTCUSDT')
    Returns:
        float: Tamaño mínimo de orden
    """
    symbol_upper = symbol.upper()
    return BITGET_MINIMUMS.get(symbol_upper, BITGET_MINIMUMS['DEFAULT'])

def get_minimum_size_string(symbol):
    """
    Obtiene el tamaño mínimo como string para compatibilidad con APIs
    Args:
        symbol (str): Símbolo del par de trading
    Returns:
        str: Tamaño mínimo como string
    """
    return str(get_minimum_size(symbol))

# Configuraciones de apalancamiento recomendadas
RECOMMENDED_LEVERAGE = {
    'BTCUSDT': 10,   # Bitcoin: hasta 10x
    'ETHUSDT': 10,   # Ethereum: hasta 10x
    'BNBUSDT': 10,   # BNB: hasta 10x
    'DEFAULT': 5     # Otros: hasta 5x por seguridad
}

def get_recommended_leverage(symbol):
    """
    Obtiene el apalancamiento recomendado para un símbolo
    Args:
        symbol (str): Símbolo del par de trading
    Returns:
        int: Apalancamiento recomendado
    """
    symbol_upper = symbol.upper()
    return RECOMMENDED_LEVERAGE.get(symbol_upper, RECOMMENDED_LEVERAGE['DEFAULT'])

# Configuraciones de precisión de precio
PRICE_PRECISION = {
    'BTCUSDT': 2,    # Bitcoin: 2 decimales
    'ETHUSDT': 2,    # Ethereum: 2 decimales
    'DEFAULT': 4     # Otros: 4 decimales por defecto
}

def get_price_precision(symbol):
    """
    Obtiene la precisión de precio recomendada para un símbolo
    Args:
        symbol (str): Símbolo del par de trading
    Returns:
        int: Número de decimales para el precio
    """
    symbol_upper = symbol.upper()
    return PRICE_PRECISION.get(symbol_upper, PRICE_PRECISION['DEFAULT'])

if __name__ == "__main__":
    # Ejemplo de uso
    print("=== Configuración de Mínimos Bitget 2025 ===")
    test_symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'UNKNOWN']
    
    for symbol in test_symbols:
        min_size = get_minimum_size(symbol)
        leverage = get_recommended_leverage(symbol)
        precision = get_price_precision(symbol)
        print(f"{symbol}: Mín={min_size}, Lev={leverage}x, Prec={precision}")
