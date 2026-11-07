"""Gunicorn settings for the backend.

Each async worker can wait on hundreds of requests at once. More workers give
you more CPU and keep things going if one crashes, but each uses memory, so on
a small server keep WEB_CONCURRENCY at 2. Caches and rate limits are per worker."""
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 150
graceful_timeout = 30     # gives background emails and syncs time to finish during a deploy
keepalive = 5
max_requests = 5000
max_requests_jitter = 500
accesslog = None          # the app logs requests itself, with request IDs
errorlog = "-"
