"""Gunicorn settings for the Flask frontend.

I use threads because each one mostly just waits on the backend. Roughly,
workers x threads = how many requests can be in flight (2 x 32 = 64 by default).
Raise GUNICORN_THREADS if traffic grows a lot."""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "32"))
timeout = 150             # a bit longer than the slowest call we proxy (the agent, 120 s)
graceful_timeout = 30
keepalive = 5
max_requests = 2000       # restart workers now and then so memory doesn't creep up
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
