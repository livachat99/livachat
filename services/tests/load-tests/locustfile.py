"""Locust load-test for LivaChat Server (skeleton).

Usage:
    cd services
    locust -f tests/load-tests/locustfile.py --host ws://localhost:8080

For headless run:
    locust -f tests/load-tests/locustfile.py --headless -u 100 -r 10 -t 2m --host ws://localhost:8080
"""

import random
import string

from locust import HttpUser, between, task


def _random_device_id() -> str:
    suffix = "".join(random.choices(string.hexdigits.lower(), k=12))
    return f"00000000-0000-0000-0000-{suffix}"


class LivaChatUser(HttpUser):
    """Simulates a single user connecting via WebSocket and completing one conversation turn."""

    wait_time = between(5, 15)

    def on_start(self):
        self.device_id = _random_device_id()

    @task
    def connect_and_ping(self):
        # WebSocket connection via HTTP upgrade (Locust doesn't natively support WS;
        # in production, use a custom client or websocket-based load tool like k6.)
        pass
