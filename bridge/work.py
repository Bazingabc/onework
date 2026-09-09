"""MyWork's Mac-only task adapter. Never accepts arbitrary CLI commands."""
import json
import subprocess
import threading
import time
import uuid
import os
from .service import AgentViewsError


class WorkService:
    def __init__(self, protocol, run=subprocess.run, clock=time.time):
        self.protocol, self.run, self.clock = protocol, run, clock
        self.lock = threading.RLock()
        self.quota_lock = threading.Lock()
        self.items = []
        self.updated = 0
        self.last_attempt = 0
        self.error = None
        self.issue = None
        self.quota = None
        self.quota_at = 0

    def cli(self, *args, timeout=45):
        try:
            result = self.run([os.environ.get('ONEWORK_LARK_CLI', 'lark-cli'), *args, '--as', 'user', '--format', 'json'],
                              capture_output=True, text=True, timeout=timeout)
            value = json.loads(result.stdout or result.stderr)
            if result.returncode != 0 or value.get('ok') is not True:
                error = value.get('error') or {}
                category = str(error.get('subtype') or error.get('type') or 'cli_error')
                self.issue = {'category':category,'code':error.get('code'),
                              'missingScopes':error.get('missing_scopes') or []}
                raise AgentViewsError('lark_' + category,
                    '飞书需要重新授权或补充权限，请打开 Mac 的「数据源」' if any(x in category for x in ('auth','scope','token','permission')) else '飞书 CLI 返回错误，请在 Mac 查看连接诊断', 503)
            self.issue = None
            return value.get('data') or {}
        except FileNotFoundError:
            self.issue = {'category':'not_installed'}
            raise AgentViewsError('lark_not_installed', '未找到飞书 CLI，请在 Mac 选择安装路径', 503)
        except subprocess.TimeoutExpired:
            self.issue = {'category':'timeout'}
            raise AgentViewsError('lark_timeout', '飞书连接超时，旧数据保留，稍后自动重试', 503)
        except (OSError, ValueError):
            self.issue = {'category':'invalid_response'}
            raise AgentViewsError('lark_unavailable', '飞书 CLI 响应异常，请在 Mac 检查版本与网络', 503)

    def tasks(self, force=False):
        with self.lock:
            if force or self.clock() - self.last_attempt >= 300:
                self.last_attempt = self.clock()
                try:
                    items, token = [], ''
                    deadline = time.monotonic() + 120
                    for _ in range(10):
                        args = ['task', '+get-my-tasks', '--complete=false', '--page-limit', '4']
                        if token:
                            args += ['--page-token', token]
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise AgentViewsError('lark_timeout', '飞书同步超时，请重试', 503)
                        data = self.cli(*args, timeout=min(45, remaining))
                        items.extend(data.get('items') or [])
                        if not data.get('has_more'):
                            break
                        next_token = data.get('page_token')
                        if not next_token or next_token == token:
                            raise AgentViewsError('lark_incomplete', '飞书分页未完成，请重试', 503)
                        token = next_token
                    else:
                        raise AgentViewsError('lark_incomplete', '待办数量超出本轮同步范围', 503)
                    unique = {i['guid']: {k: i.get(k) for k in ('guid', 'summary', 'due_at', 'url')}
                              for i in items if i.get('guid') and not i.get('completed')}
                    self.items = sorted(unique.values(), key=lambda i: i.get('due_at') or '9999')
                    self.updated, self.error = int(self.clock()), None
                except AgentViewsError as exc:
                    self.error = str(exc)
            return {'items': list(self.items), 'updatedAt': self.updated, 'error': self.error,
                    'stale': self.error is not None}

    def complete(self, identifier):
        try:
            uuid.UUID(identifier)
        except (ValueError, TypeError):
            raise AgentViewsError('task_invalid', '任务标识无效', 400)
        with self.lock:
            # Only permit targets supplied by this user's task list.
            if not any(i['guid'] == identifier for i in self.items):
                raise AgentViewsError('task_changed', '任务已变化，请刷新列表', 409)
            params = json.dumps({'task_guid': identifier})
            before = self.cli('task', 'tasks', 'get', '--params', params).get('task') or {}
            if before.get('status') != 'done':
                try:
                    self.cli('task', '+complete', '--task-id', identifier)
                except AgentViewsError:
                    # A transport timeout may follow a successful server write.
                    pass
            try:
                after = self.cli('task', 'tasks', 'get', '--params', params).get('task') or {}
            except AgentViewsError:
                raise AgentViewsError('completion_unconfirmed', '等待云端确认，请刷新核实；任务暂时保留', 503)
            if after.get('status') != 'done' or str(after.get('completed_at') or '0') == '0':
                raise AgentViewsError('completion_unconfirmed', '云端尚未确认整个任务完成，请在飞书核实会签状态', 409)
            self.items = [i for i in self.items if i['guid'] != identifier]
            return {'confirmed': True, 'guid': identifier}

    def verify(self, identifier):
        try:
            uuid.UUID(identifier)
        except (ValueError, TypeError):
            raise AgentViewsError('task_invalid', '任务标识无效', 400)
        with self.lock:
            task = self.cli('task', 'tasks', 'get', '--params', json.dumps({'task_guid': identifier})).get('task') or {}
            confirmed = task.get('status') == 'done' and str(task.get('completed_at') or '0') != '0'
            if confirmed:
                self.items = [i for i in self.items if i['guid'] != identifier]
            return {'confirmed': confirmed, 'guid': identifier, 'verified': True}

    def usage(self):
        with self.quota_lock:
            if self.clock() - self.quota_at >= 60:
                self.quota_at = self.clock()
                try:
                    self.quota = self.protocol.request('account/rateLimits/read', {})
                    self.quota = {key: self.quota.get(key) for key in ('rateLimits', 'rateLimitsByLimitId')}
                    return {'available': True, 'data': self.quota}
                except Exception:
                    self.quota = None
            return {'available': self.quota is not None, 'data': self.quota}
