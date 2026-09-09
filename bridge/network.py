"""Recover only the TLS listener when the Mac's private network changes.

The local service, Codex generation, device store, certificate and operation
ledger remain unchanged. Already accepted requests are never replayed here.
"""
import subprocess
import threading
from .pairing import private_origin


def private_address():
    for name in ('en0', 'en1', 'en2', 'en3', 'en4', 'en5', 'en6', 'en7'):
        try:
            value = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', name],
                                   capture_output=True, text=True, timeout=1).stdout.strip()
            private_origin('https://' + value + ':8766')
            return value
        except (ValueError, OSError, subprocess.TimeoutExpired):
            continue
    return None


class LanRecovery:
    def __init__(self, local, port, directory, *, address=private_address, factory=None, interval=3):
        if factory is None:
            from .lan import make_lan_server
            factory = make_lan_server
        self.local, self.port, self.directory = local, port, directory
        self.address, self.factory, self.interval = address, factory, interval
        self.listener = self.worker = self.thread = None
        self.host = None
        self.stopped = threading.Event()
        self.set_state('connecting', '正在建立局域网连接')

    def set_state(self, state, message):
        self.local.product.network = {'state': state, 'message': message,
            'retrySeconds': self.interval if state != 'ready' else None}

    def release(self):
        listener, worker = self.listener, self.worker
        self.listener = self.worker = None
        self.host = None
        self.local.product.lan_url = None
        if listener:
            if worker and worker.is_alive():
                listener.shutdown()
            listener.server_close()
        if worker:
            worker.join(timeout=2)

    def reconcile(self, host):
        if not host:
            self.release()
            self.set_state('offline', '未连接可用局域网；网络恢复后自动重连，已有配对保留')
            return
        try:
            private_origin('https://%s:%s' % (host, self.port))
        except ValueError:
            self.release()
            self.set_state('offline', '网络地址不可用，等待私网连接')
            return
        if host == self.host and self.worker and self.worker.is_alive():
            self.set_state('ready', '局域网连接正常')
            return
        # Stop accepting on the stale address without killing the bridge or
        # any Codex process. Existing request workers may finish normally.
        self.release()
        self.set_state('recovering', '网络已变化，正在恢复平板连接；不会自动重发操作')
        candidate = None
        try:
            candidate = self.factory(self.local, host, self.port, self.directory)
            worker = threading.Thread(target=candidate.serve_forever, daemon=True)
            worker.start()
            self.listener, self.worker, self.host = candidate, worker, host
            self.local.product.lan_url = 'https://%s:%s' % (host, self.port)
            self.set_state('ready', '局域网连接正常')
        except Exception:
            if candidate:
                candidate.server_close()
            self.set_state('recovering', '局域网监听暂不可用，正在自动重试；请检查网络或端口占用')

    def start(self):
        self.reconcile(self.address())
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.stopped.wait(self.interval):
                self.reconcile(self.address())
        finally:
            self.release()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=12)
        else:
            self.release()
