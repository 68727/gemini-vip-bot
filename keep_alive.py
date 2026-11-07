import os
import logging
from threading import Thread
from flask import Flask

# Configure logger
logger = logging.getLogger("keep_alive")

server = Flask(__name__)

@server.route("/")
def home():
    return "🤖 Gemini VIP Telegram Bot is alive and running 24/7!"

@server.route("/health")
def health():
    return {"status": "ok", "service": "gemini-vip-bot"}, 200

def run():
    # Render provides PORT in the environment; default to 8080 for local testing
    port = int(os.environ.get("PORT", 8080))
    # Werkzeug logger can be noisy; keep it at error level
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    logger.info(f"Keep-alive web server listening on port {port}")
    server.run(host="0.0.0.0", port=port)

def keep_alive():
    """Starts the Flask web server in a daemon thread so it runs in the background."""
    t = Thread(target=run, daemon=True)
    t.start()

