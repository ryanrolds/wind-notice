"""Gunicorn configuration for wind_notice."""

import os

bind = "0.0.0.0:5000"
workers = 1
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "4"))
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
