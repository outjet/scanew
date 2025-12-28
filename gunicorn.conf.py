import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gevent"
worker_connections = int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", "1000"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
