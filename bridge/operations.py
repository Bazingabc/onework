"""Durable at-most-once dispatch within this bridge; unknown upstream results never replay."""
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from .service import AgentViewsError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class OperationStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        Path(path).chmod(0o600)
        self.lock = threading.RLock()
        self.targets = {}
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, device TEXT, target TEXT, hash TEXT, state TEXT, result TEXT, created INTEGER)')
            # A crash after dispatch cannot be distinguished from upstream success.
            self.db.execute("UPDATE operations SET state='unknown' WHERE state='dispatching'")

    def revision(self, target, value, epoch):
        with self.lock:
            row = self.db.execute('SELECT count(*) FROM operations WHERE target=?', (target,)).fetchone()
        return digest([epoch, row[0], value])

    def inspect(self, identifier, device):
        with self.lock:
            row = self.db.execute('SELECT device,state,result FROM operations WHERE id=?', (identifier,)).fetchone()
        if not row or row[0] != device:
            raise AgentViewsError('operation_missing', '找不到当前设备的操作记录', 404)
        return {'operationId': identifier, 'state': row[1], 'result': json.loads(row[2]) if row[2] else None}

    def recent(self):
        with self.lock:
            rows = self.db.execute('SELECT id,device,target,state,created FROM operations ORDER BY created DESC LIMIT 30').fetchall()
        return [dict(zip(('operationId','deviceId','target','state','createdAt'), row)) for row in rows]

    def execute(self, identifier, device, target, payload, validate, dispatch):
        try:
            uuid.UUID(identifier)
        except (ValueError, TypeError, AttributeError):
            raise AgentViewsError('operation_id_required', '请更新平板：写操作必须携带有效操作编号', 428)
        signature = digest([target, payload])
        with self.lock:
            target_lock = self.targets.setdefault(target, threading.RLock())
        with target_lock:
            with self.lock:
                old = self.db.execute('SELECT device,hash,state,result FROM operations WHERE id=?', (identifier,)).fetchone()
                if old:
                    if old[0] != device or old[1] != signature:
                        raise AgentViewsError('operation_conflict', '操作编号已用于不同请求', 409)
                    if old[2] == 'succeeded':
                        return json.loads(old[3])
                    raise AgentViewsError('operation_unknown', '该操作结果待确认，请勿重新发送；请在 Mac 核实', 409)
                unresolved = self.db.execute("SELECT 1 FROM operations WHERE target=? AND state IN ('unknown','dispatching')", (target,)).fetchone()
                if unresolved:
                    raise AgentViewsError('operation_unknown', '此目标存在结果待确认的操作，请先在 Mac 核实', 409)
            validate()
            with self.lock, self.db:
                try:
                    self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                                    (identifier, device, target, signature, 'dispatching', None, int(time.time())))
                except sqlite3.IntegrityError:
                    raise AgentViewsError('operation_conflict', '操作编号冲突，请核实原操作', 409)
            try:
                result = dispatch()
            except Exception as exc:
                if isinstance(exc, AgentViewsError) and exc.code in {'pending_changed','message_required','message_too_long','answers_invalid','answers_incomplete','structured_answers_required','approval_not_pending','approval_decision_invalid','approval_response_required','permission_request_invalid','approval_kind_unsupported'}:
                    with self.lock, self.db:
                        self.db.execute("UPDATE operations SET state='rejected' WHERE id=?", (identifier,))
                    raise
                # Even an upstream error can occur after a write. Fail closed.
                with self.lock, self.db:
                    self.db.execute("UPDATE operations SET state='unknown' WHERE id=?", (identifier,))
                raise AgentViewsError('operation_unknown', '提交结果待确认。请勿重复提交，请到 Mac 核实实际结果', 409)
            with self.lock, self.db:
                self.db.execute("UPDATE operations SET state='succeeded',result=? WHERE id=?", (json.dumps(result), identifier))
            return result

    def resolve_verified(self, target, result):
        with self.lock, self.db:
            self.db.execute("UPDATE operations SET state='succeeded',result=? WHERE target=? AND state='unknown'",
                            (json.dumps(result), target))

    def close(self):
        self.db.close()
