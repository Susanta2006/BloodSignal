"""Temporary connectivity check. Run on the server you want to test, then delete.

    python -m app.diag_egress
Prints where the connection to e-RaktKosh succeeds or fails (DNS, TCP, TLS, HTTP).
TLS verification stays enabled. Makes 1 request to the same endpoint the app uses.
"""
import socket
import ssl
import time

import httpx

HOST = "eraktkosh.mohfw.gov.in"
URL = "https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/blood-availability"


def step(name, fn):
    t = time.monotonic()
    try:
        print(f"[diag] {name}: OK {fn()} ({time.monotonic() - t:.1f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[diag] {name}: FAIL {type(e).__name__} {e} ({time.monotonic() - t:.1f}s)", flush=True)


def tcp():
    socket.create_connection((HOST, 443), timeout=15).close()
    return "connected"


def tls():
    s = socket.create_connection((HOST, 443), timeout=15)
    w = ssl.create_default_context().wrap_socket(s, server_hostname=HOST)
    v = w.version()
    w.close()
    return v


step("egress IP", lambda: httpx.get("https://api.ipify.org", timeout=10).text)
step("DNS", lambda: sorted({a[4][0] for a in socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)}))
step("TCP 443", tcp)
step("TLS", tls)
step("HTTP", lambda: httpx.get(
    URL, params={"stateCode": "16", "componentId": "12"}, headers={"Accept": "application/json"},
    timeout=httpx.Timeout(30, connect=15), follow_redirects=False).status_code)
