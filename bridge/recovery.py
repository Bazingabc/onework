"""Keep the bridge and Feishu available when the owned Codex adapter is unavailable."""
import threading
import uuid
from .service import AgentViewsError


class RecoveringService:
    def __init__(self, factory):
        self.factory = factory
        self.current = None
        self.last_error = '正在连接 Codex'
        self.last_disconnect = None
        self.generation = uuid.uuid4().hex
        self.stop = threading.Event()
        self.thread = None
        self.protocol = self  # WorkService resolves the active protocol on each request.

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self.health()

    def _run(self):
        attempts = 0
        while not self.stop.is_set():
            active = self.current
            if active is not None:
                process = getattr(active.protocol, 'process', None)
                if process is not None and process.poll() is None:
                    if self.stop.wait(3): break
                    continue
                self.current = None
                self.generation = uuid.uuid4().hex
                self.last_disconnect = active.health().get('lastError') or 'Codex 子进程已退出'
                active.close()
                self.last_error = str(self.last_disconnect)[:300] + '；正在恢复，旧审批已失效'
            if attempts >= 5:
                self.last_error = 'Codex 连续启动失败。请在 Mac 检查路径、版本与登录后重启连接'
                self.stop.wait()
                break
            candidate = None
            try:
                candidate = self.factory()
                candidate.start()
                if self.stop.is_set():
                    candidate.close()
                    break
                self.current = candidate
                self.generation = uuid.uuid4().hex
                self.last_error = None
                attempts = 0
            except Exception as exc:
                if candidate: candidate.close()
                self.last_error = str(exc)[:300]
                attempts += 1
                self.stop.wait(min(60, 5 * 2 ** (attempts-1)))

    def health(self):
        active = self.current
        if active:
            return dict(active.health(), generation=self.generation, lastDisconnect=self.last_disconnect)
        return {'ok':False, 'lastError':self.last_error, 'generation':self.generation,
                'lastDisconnect':self.last_disconnect}

    def request(self, *args, **kwargs):
        active=self.current
        if not active: raise AgentViewsError('codex_unavailable', self.last_error or 'Codex 未连接', 503)
        return active.protocol.request(*args, **kwargs)

    def __getattr__(self, name):
        active=self.current
        if not active: raise AgentViewsError('codex_unavailable', self.last_error or 'Codex 未连接', 503)
        return getattr(active, name)

    def close(self):
        self.stop.set()
        if self.current: self.current.close()
        if self.thread: self.thread.join(timeout=3)
