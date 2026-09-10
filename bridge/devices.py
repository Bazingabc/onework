"""Two-stage device authorization; only the local Mac controller grants access."""
import hmac
import secrets
from .pairing import PairStore
from .service import AgentViewsError


class DeviceStore(PairStore):
    @staticmethod
    def metadata(value):
        if not isinstance(value, dict): return {}
        result = {}
        for key in ('brand','manufacturer','model','productName','androidVersion'):
            field = value.get(key)
            if isinstance(field, str):
                result[key] = ''.join(c for c in field if c.isprintable()).strip()[:80]
        if value.get('category') in ('tablet','phone','tv','watch','car','unknown'):
            result['category'] = value['category']
        return result

    def update_metadata(self, identifier, value):
        metadata = self.metadata(value)
        with self.transaction():
            devices = self._read(self.devices_path)
            if identifier not in devices:
                raise AgentViewsError('device_unauthorized', '设备已撤销，请重新配对', 401)
            if devices[identifier].get('metadata') != metadata:
                devices[identifier]['metadata'] = metadata
                self._save(self.devices_path, devices)
        return {'updated': True}

    def __init__(self, directory, **kwargs):
        super().__init__(directory, **kwargs)
        self.pending_path = self.directory / 'pending-devices.json'

    def issue(self, origin, fingerprint):
        value = super().issue(origin, fingerprint)
        value['version'] = 2
        return value

    def begin(self, code, name, token, metadata=None):
        if not isinstance(token, str) or not 40 <= len(token) <= 128 or not isinstance(code, str) or not 32 <= len(code) <= 128:
            raise AgentViewsError('pair_invalid', '配对凭证格式无效', 400)
        hashed = self.digest(token)
        with self.transaction():
            pending = self._read(self.pending_path)
            pending = {k:v for k,v in pending.items() if v['expiresAt'] > self.clock()}
            devices = self._read(self.devices_path)
            for identifier, value in pending.items():
                if value['hash'] == hashed and value['inviteHash'] == self.digest(code):
                    return {'requestId': identifier, 'state': self._approval_state(identifier, value, devices)}
            invite = self._read(self.invite_path)
            if invite.get('used') or self.clock() >= invite.get('expiresAt', 0) or not hmac.compare_digest(invite.get('hash', ''), self.digest(code)):
                raise AgentViewsError('pair_expired', '二维码已过期或已使用，请在 Mac 生成新码', 403)
            if sum(self._approval_state(k, v, devices) == 'pending' for k, v in pending.items()) >= 20:
                raise AgentViewsError('devices_full', '待确认申请已达上限，请稍后重试', 409)
            identifier = secrets.token_hex(16)
            pending[identifier] = {'name': str(name or 'Android')[:80], 'hash': hashed,
                'inviteHash': self.digest(code), 'state': 'pending', 'expiresAt': int(self.clock()) + 300,
                'metadata': self.metadata(metadata)}
            invite['used'] = True
            self._save(self.invite_path, invite)
            self._save(self.pending_path, pending)
            return {'requestId': identifier, 'state': 'pending'}

    def pair_status(self, identifier, token):
        if not isinstance(token,str) or len(token)>128:
            raise AgentViewsError('pair_invalid', '配对凭证无效', 403)
        with self.transaction():
            devices = self._read(self.devices_path)
            device = devices.get(identifier, {})
            # The authorization file is authoritative even if the second file
            # was not written, or its short-lived pending entry was pruned.
            if token and hmac.compare_digest(device.get('hash', ''), self.digest(token)):
                return {'requestId': identifier, 'state': 'approved', 'role': device.get('role', 'control')}
            value = self._read(self.pending_path).get(identifier, {})
            if not token or not hmac.compare_digest(value.get('hash', ''), self.digest(token)):
                raise AgentViewsError('pair_invalid', '配对申请不存在', 403)
            if self.clock() >= value.get('expiresAt', 0):
                raise AgentViewsError('pair_expired', '配对申请已过期，请重新扫码', 403)
            state = self._approval_state(identifier, value, devices)
            return {'requestId': identifier, 'state': state, 'role': value.get('role') if state == 'approved' else None}

    @staticmethod
    def _approval_state(identifier, value, devices):
        if identifier in devices and devices[identifier].get('hash') == value.get('hash'):
            return 'approved'
        if value['state'] == 'approved': return 'denied'
        if value['state'] == 'replacing':
            return 'pending' if value.get('replacement', {}).get('oldDeviceId') in devices else 'denied'
        return value['state']

    def _online(self, value):
        return value.get('lastSeen', 0) > 0 and self.clock() - value['lastSeen'] < 30

    def replace(self, identifier, old_identifier, expected_role, confirm_online=False):
        """Local-only compare-and-replace. Never reconstruct authorization from pending.

        A durable intent prevents ordinary approval after interrupted replacement.
        The single devices.json rename removes old and grants new atomically.
        The receipt lives only in the new authorization, so revocation wins retries.
        """
        if not identifier or not old_identifier or identifier == old_identifier:
            raise ValueError('请选择不同的已有配对')
        if expected_role not in ('view', 'control'):
            raise ValueError('请刷新并确认原配对权限')
        intent = {'oldDeviceId': old_identifier, 'expectedRole': expected_role}
        with self.transaction():
            devices = self._read(self.devices_path)
            committed = devices.get(identifier)
            if committed:
                if committed.get('replacement') != intent:
                    raise ValueError('申请已由其他操作处理，请刷新')
                return self._replacement_result(identifier, committed)
            pending = self._read(self.pending_path)
            value = pending.get(identifier)
            if not value or value.get('state') not in ('pending', 'replacing') or value['expiresAt'] <= self.clock():
                raise ValueError('配对申请已处理或过期，请刷新；已提交的替换不会恢复旧授权')
            if value['state'] == 'replacing' and value.get('replacement', {}).get('oldDeviceId') != old_identifier:
                raise ValueError('该申请已有替换操作，请核实原目标后重试')
            old = devices.get(old_identifier)
            if not old:
                raise ValueError('原配对已失效或替换结果已被撤销，请刷新；不会恢复任何授权')
            role = old.get('role', 'control')
            if role != expected_role:
                raise ValueError('原配对权限已变化，请刷新并重新选择')
            if self._online(old) and confirm_online is not True:
                raise ValueError('原配对现在在线，请刷新并单独确认断开旧连接')
            if any(hmac.compare_digest(v.get('hash', ''), value['hash']) for v in devices.values()):
                raise ValueError('新客户端必须使用新的配对凭证，请重新扫码')
            # Record non-authorizing intent BEFORE committing devices, so a
            # crash followed by revocation can never fall back to normal approval.
            value['state'] = 'replacing'
            value['replacement'] = intent
            self._save(self.pending_path, pending)
            new = {'hash': value['hash'], 'name': value['name'], 'role': role,
                   'pairedAt': int(self.clock()), 'lastSeen': 0,
                   'metadata': value.get('metadata', {}), 'replacement': intent}
            del devices[old_identifier]
            devices[identifier] = new
            try:
                self._save(self.devices_path, devices)
            except OSError:
                # A response/error after rename is not evidence of rollback.
                saved = self._read(self.devices_path).get(identifier, {})
                if saved.get('replacement') != intent or saved.get('hash') != new['hash']:
                    raise
            value['state'], value['role'] = 'approved', role
            try:
                self._save(self.pending_path, pending)
            except OSError:
                pass  # Recover status from the committed authorization, not intent.
            return self._replacement_result(identifier, new)

    def _replacement_result(self, identifier, device):
        return {'requestId': identifier, 'state': 'approved', 'role': device.get('role', 'control'),
                'device': {'deviceId': identifier, 'name': device.get('name'), 'metadata': device.get('metadata', {}),
                           'role': device.get('role', 'control'), 'pairedAt': device.get('pairedAt'),
                           'lastSeen': device.get('lastSeen', 0), 'online': self._online(device)},
                'message': '已替换配对，旧连接后续请求将被拒绝；已受理操作不会撤回'}

    def approve(self, identifier, role):
        if role not in ('view', 'control', 'denied'):
            raise ValueError('设备权限无效')
        with self.transaction():
            pending = self._read(self.pending_path)
            value = pending.get(identifier)
            if not value or (value['state'] != 'pending' and not (role == 'denied' and value['state'] == 'replacing')) or value['expiresAt'] <= self.clock():
                raise ValueError('配对申请已处理或过期')
            devices = self._read(self.devices_path)
            if identifier in devices:
                raise ValueError('申请已批准，请刷新')
            if role != 'denied':
                if len(devices) >= 20: raise ValueError('设备已达上限')
                devices[identifier] = {'hash': value['hash'], 'name': value['name'], 'role': role, 'pairedAt': int(self.clock()), 'lastSeen': 0,
                                       'metadata': value.get('metadata', {})}
                self._save(self.devices_path, devices)
            value['state'] = 'denied' if role == 'denied' else 'approved'
            value['role'] = role
            self._save(self.pending_path, pending)

    def devices(self):
        with self.transaction():
            return [dict(deviceId=k, name=v.get('name'), metadata=v.get('metadata', {}), role=v.get('role', 'control'),
                         pairedAt=v.get('pairedAt'), lastSeen=v.get('lastSeen', 0),
                         online=self._online(v))
                    for k,v in self._read(self.devices_path).items()]

    def pending(self):
        with self.transaction():
            devices = self._read(self.devices_path)
            return [dict(requestId=k,name=v.get('name'),metadata=v.get('metadata',{}),expiresAt=v['expiresAt'])
                    for k,v in self._read(self.pending_path).items()
                    if v['state'] in ('pending', 'replacing') and v['expiresAt']>self.clock()
                    and self._approval_state(k, v, devices) == 'pending']

    def set_role(self, identifier, role):
        if role not in ('view','control'): raise ValueError('设备权限无效')
        with self.transaction():
            devices=self._read(self.devices_path)
            if identifier not in devices: raise ValueError('设备不存在')
            devices[identifier]['role']=role
            self._save(self.devices_path,devices)

    def identity(self, token):
        if not token or len(token)>128: return None
        hashed = self.digest(token)
        with self.transaction():
            devices = self._read(self.devices_path)
            for identifier, value in devices.items():
                if hmac.compare_digest(value.get('hash',''), hashed):
                    if not self._online(value) and sum(self._online(v) for v in devices.values())>=3:
                        raise AgentViewsError('device_limit', '当前已有 3 台设备在线，请断开另一台后重试', 429)
                    if not value.get('lastSeen') or self.clock()-value.get('lastSeen',0)>=10:
                        value['lastSeen']=int(self.clock())
                        self._save(self.devices_path,devices)
                    return {'deviceId':identifier,'role':value.get('role','control')}
        return None
