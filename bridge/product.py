"""Product protocol, cached source health, revision-checked business actions."""
import uuid
from .operations import OperationStore
from .service import AgentViewsError
from .snapshots import ReadSnapshots


class ProductRuntime:
    def __init__(self, service, work, directory):
        self.service, self.work = service, work
        self.epoch = uuid.uuid4().hex
        self.operations = OperationStore(directory / 'operations.sqlite3')
        generation = lambda: getattr(self.service, 'generation', '')
        self.list_snapshots = ReadSnapshots(generation)
        self.detail_snapshots = ReadSnapshots(generation, workers=2, capacity=16)

    def sessions(self):
        values, snapshot = self.list_snapshots.read('list', lambda: self.service.list_sessions(strict=True))
        stale = snapshot['state'] != 'fresh'
        return {'sessions': values or [], 'stale': stale, 'snapshot': snapshot,
                'error': '正在同步 Codex 列表' if snapshot['state'] == 'loading' else
                         '列表快照已过期，正在重试' if stale else None,
                'serverEpoch': self.epoch}

    def session(self, identifier):
        generation = getattr(self.service, 'generation', '')
        value, snapshot = self.detail_snapshots.read(identifier, lambda: self.service.get_session(identifier, strict=True))
        result = self._session_revision(identifier, value) if value is not None else {
            'session': None, 'revision': '', 'serverEpoch': self.epoch}
        result.update(snapshot=snapshot, stale=snapshot['state'] != 'fresh')
        if generation != getattr(self.service, 'generation', ''):
            raise AgentViewsError('object_changed', 'Codex 连接已恢复，请刷新后操作', 409)
        if result['stale']:
            result['revision'] = ''  # Read snapshots cannot authorize an outdated action.
            result['error'] = '正在读取任务详情，稍后自动更新' if snapshot['state'] == 'loading' else '详情读取失败或快照已过期，正在重试'
        return result

    def _session_revision(self, identifier, value):
        # No generated timestamps: only the state the user can act on.
        evidence = {k: value.get(k) for k in ('id','updatedAt','state','status','pending','attention','control','messages')}
        revision = self.operations.revision('session:' + identifier, evidence, self.epoch + str(getattr(self.service, 'generation', '')))
        return {'session': value, 'revision': revision, 'serverEpoch': self.epoch}

    def _current_session(self, identifier):
        generation = getattr(self.service, 'generation', '')
        value = self.service.get_session(identifier, strict=True)
        if generation != getattr(self.service, 'generation', ''):
            raise AgentViewsError('object_changed', 'Codex 连接已恢复，请刷新后操作', 409)
        return self._session_revision(identifier, value)

    def close(self):
        self.list_snapshots.close()
        self.detail_snapshots.close()
        self.operations.close()

    def tasks(self, force=False):
        value = dict(self.work.tasks(force=force))
        value['items'] = [dict(item, revision=self.operations.revision('task:' + item['guid'], item, self.epoch)) for item in value['items']]
        value['serverEpoch'] = self.epoch
        return value

    def status(self):
        health = self.service.health()
        return {'protocol': {'major': 2, 'minor': 0}, 'serverEpoch': self.epoch, 'bridge': 'ready', 'lanUrl': getattr(self, 'lan_url', None),
                'network': getattr(self, 'network', {'state': 'ready' if getattr(self, 'lan_url', None) else 'offline', 'message': '局域网监听未启用' if not getattr(self, 'lan_url', None) else '局域网连接正常'}),
                'codex': {'state': 'ready' if health.get('ok') else 'unavailable',
                          'version': health.get('codexVersion'), 'error': health.get('lastError')},
                'lark': {'state': 'unavailable' if self.work.error else 'ready' if self.work.updated else 'not_checked',
                         'updatedAt': self.work.updated, 'error': self.work.error,
                         'issue': getattr(self.work, 'issue', None)},
                'operations': self.operations.recent()}

    def action(self, actor, body):
        kind, target = body.get('kind'), str(body.get('target') or '')
        payload = body.get('payload') or {}
        if not isinstance(payload, dict) or not target or len(target) > 200:
            raise AgentViewsError('action_invalid', '操作参数无效', 400)
        if actor.get('role') != 'control':
            raise AgentViewsError('device_read_only', '此设备为仅查看，请在 Mac 修改设备权限', 403)
        if kind not in ('message','approval','dismiss','complete','create'):
            raise AgentViewsError('action_unsupported', '不支持此操作', 400)
        key = 'create:'+actor['deviceId'] if kind == 'create' else ('task:' if kind == 'complete' else 'session:') + target
        pending_id = ''

        def validate():
            nonlocal pending_id
            if kind == 'message':
                message = payload.get('text') or ''
                answers = payload.get('answers')
                if not isinstance(message, str) or len(message) > 20000 or (not message.strip() and not answers):
                    raise AgentViewsError('message_invalid', '请输入 1 至 20000 字符或选择问题答案', 400)
                if answers is not None and not isinstance(answers, dict):
                    raise AgentViewsError('answers_invalid', '问题答案格式无效', 400)
            if kind == 'approval' and payload.get('decision') not in ('accept','acceptForSession','decline','cancel'):
                raise AgentViewsError('approval_decision_invalid', '审批决定无效', 400)
            if kind == 'create':
                revision = self.epoch
            elif kind == 'complete':
                tasks = self.tasks()
                if tasks.get('stale'):
                    raise AgentViewsError('source_stale', '待办已过期，请刷新或恢复飞书连接', 409)
                revision = next((x['revision'] for x in tasks['items'] if x['guid'] == target), None)
            else:
                detail = self._current_session(target)
                revision = detail['revision']
                pending_id = str((detail['session'].get('pending') or {}).get('requestId') or '')
            if not revision or revision != body.get('expectedRevision'):
                raise AgentViewsError('object_changed', '内容已变化或连接已恢复，请查看最新内容后再操作；原输入保留', 409)

        def dispatch():
            if kind == 'create':
                result = {'session': self.service.create_session(payload.get('initialText'))}
            elif kind == 'complete':
                result = self.work.complete(target)
            elif kind == 'message':
                result = self.service.send(target, payload.get('text'), answers=payload.get('answers'),
                    expected_message_id=payload.get('expectedMessageId'), expected_updated_at=payload.get('expectedUpdatedAt'), expected_pending_id=pending_id)
            elif kind == 'approval':
                result = self.service.respond_approval(target, str(payload.get('decision') or ''), expected_pending_id=pending_id)
            else:
                result = self.service.dismiss_attention(target, payload.get('messageId'))
            return result

        try:
            return self.operations.execute(body.get('operationId'), actor['deviceId'], key, body, validate, dispatch)
        finally:
            # Even an uncertain write must not leave pre-action evidence marked fresh.
            self.list_snapshots.invalidate('list')
            self.detail_snapshots.invalidate(target)
