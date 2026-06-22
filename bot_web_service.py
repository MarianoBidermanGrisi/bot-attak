"""
bot_web_service.py — Entry point for Render.com Web Service
==========================================================
Run: python bot_web_service.py
- Starts HTTP health server on PORT (env) or 8080
- Runs TurtleBot in background
- Handles graceful shutdown (SIGTERM)
"""
import os, sys, asyncio, logging, signal, json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger('web_service')

from bot_turtle import TurtleBot, API_KEY, API_SECRET, API_PASSWORD

# ==========================================
# HEALTH SERVER
# ==========================================
async def health_server(bot):
    """Servidor HTTP mínimo para health checks de Render."""
    async def handler(reader, writer):
        try:
            bal = 0.0
            if bot and bot.ex and bot.ex.ex:
                bal = await bot.ex.get_usdt_balance()
            open_n = len(bot.tracker.positions) if bot and bot.tracker else 0
            trades_n = len(bot.tracker.trades_log) if bot and bot.tracker else 0
            body = json.dumps({
                'status': 'ok',
                'balance': round(bal, 2),
                'open_positions': open_n,
                'total_trades': trades_n,
                'uptime_hours': None,
            })
            response = (
                'HTTP/1.1 200 OK\r\n'
                'Content-Type: application/json\r\n'
                f'Content-Length: {len(body)}\r\n'
                'Connection: close\r\n'
                '\r\n'
                f'{body}'
            )
            writer.write(response.encode())
            await writer.drain()
        except Exception as e:
            log.warning(f"Health handler error: {e}")
        finally:
            writer.close()

    port = int(os.environ.get('PORT', 8080))
    server = await asyncio.start_server(handler, '0.0.0.0', port)
    log.info(f"Health server listening on :{port}")
    async with server:
        await server.serve_forever()

# ==========================================
# ENTRY POINT
# ==========================================
async def main():
    # Verificar credenciales
    if not API_KEY or not API_SECRET or not API_PASSWORD:
        log.error("FALTAN CREDENCIALES. Setear BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE en Render")
        sys.exit(1)

    # Callback para notificar trades nuevos
    def on_trade(trade):
        log.info(f"NUEVO TRADE: {trade['symbol']} {trade['side']} pnl={trade['pnl']:.2f}")

    bot = TurtleBot(on_trade=on_trade)

    # Manejar señal de parada (Render envía SIGTERM)
    def shutdown(sig=None, frame=None):
        log.info(f"Señal de parada recibida ({sig}). Apagando...")
        bot.stop()

    # Windows no soporta signal.signal(SIGTERM); Linux sí
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (ValueError, AttributeError):
        log.warning("SIGTERM no soportado en este SO (esperado en Windows)")
    signal.signal(signal.SIGINT, shutdown)

    # Correr bot + health server concurrentemente
    try:
        await asyncio.gather(
            bot.run(),
            health_server(bot),
        )
    except Exception as e:
        log.error(f"Error fatal: {e}", exc_info=True)
        bot.stop()

if __name__ == '__main__':
    asyncio.run(main())
