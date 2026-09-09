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
            for identifier, value in pending.items():
                if value['hash'] == hashed and value['inviteHash'] == self.digest(code):
                    return {'requestId': identifier, 'state': value['state']}
            invite = self._read(self.invite_path)
            if invite.get('used') or self.clock() >= invite.get('expiresAt', 0) or not hmac.compare_digest(invite.get('hash', ''), self.digest(code)):
                raise AgentViewsError('pair_expired', '二维码已过期或已使用，请在 Mac 生成新码', 403)
            if len(pending) >= 20 or len(self._read(self.devices_path)) >= 20:
                raise AgentViewsError('devices_full', '请在 Mac 移除不使用的设备', 409)
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
            value = self._read(self.pending_path).get(identifier, {})
            if not token or not hmac.compare_digest(value.get('hash', ''), self.digest(token)):
                raise AgentViewsError('pair_invalid', '配对申请不存在', 403)
            if self.clock() >= value.get('expiresAt', 0):
                raise AgentViewsError('pair_expired', '配对申请已过期，请重新扫码', 403)
            return {'requestId': identifier, 'state': value['state'], 'role': value.get('role')}

    def approve(self, identifier, role):
        if role not in ('view', 'control', 'denied'):
            raise ValueError('设备权限无效')
        with self.transaction():
            pending = self._read(self.pending_path)
            value = pending.get(identifier)
            if not value or value['state'] != 'pending' or value['expiresAt'] <= self.clock():
                raise ValueError('配对申请已处理或过期')
            devices = self._read(self.devices_path)
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
                         online=self.clock()-v.get('lastSeen', 0)<30)
                    for k,v in self._read(self.devices_path).items()]

    def pending(self):
        with self.transaction():
            return [dict(requestId=k,name=v.get('name'),metadata=v.get('metadata',{}),expiresAt=v['expiresAt'])
                    for k,v in self._read(self.pending_path).items() if v['state']=='pending' and v['expiresAt']>self.clock()]

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
                    if self.clock()-value.get('lastSeen',0)>=30 and sum(self.clock()-v.get('lastSeen',0)<30 for v in devices.values())>=3:
                        raise AgentViewsError('device_limit', '当前已有 3 台设备在线，请断开另一台后重试', 429)
                    if self.clock()-value.get('lastSeen',0)>=10:
                        value['lastSeen']=int(self.clock())
                        self._save(self.devices_path,devices)
                    return {'deviceId':identifier,'role':value.get('role','control')}
        return None
