"""Ephemeral native-to-WebView bootstrap; public HTTP pages contain no secrets."""
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError


class LocalAccess:
    def __init__(self):
        self.lock = threading.RLock()
        self.rotate()

    def rotate(self):
        with self.lock:
            self.token = secrets.token_urlsafe(48)
            self.csrf = secrets.token_urlsafe(32)
            self.tickets = {}

    def issue(self):
        with self.lock:
            now = time.monotonic()
            self.tickets = {k: v for k, v in self.tickets.items() if v > now}
            if len(self.tickets) >= 8:
                self.tickets.clear()
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = now + 30
            return ticket

    def redeem(self, ticket):
        if not isinstance(ticket, str) or len(ticket) > 128:
            return None
        with self.lock:
            expires = self.tickets.pop(ticket, 0)
            return self.token if expires > time.monotonic() else None

    @staticmethod
    def cookie_name(port):
        return "polyu_console_" + str(port)

    def authorized(self, headers, port):
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get("Cookie", ""))
            item = cookie.get(self.cookie_name(port))
            supplied = item.value if item else ""
            return bool(supplied) and secrets.compare_digest(supplied, self.token)
        except (CookieError, TypeError, ValueError):
            return False
