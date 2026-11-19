"""One metrics registry per replica; scale with replicas, not worker processes."""

import os

bind = f"0.0.0.0:{int(os.getenv('PORT', '8000'))}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
preload_app = False
# Writable even with a read-only root filesystem when /tmp is a tmpfs mount.
worker_tmp_dir = "/tmp"