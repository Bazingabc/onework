"""Local-only pairing administration and persisted device credentials."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .service import AgentViewsError


def private_origin(origin):
    parsed = urlsplit(origin)
    try:
        address = ipaddress.ip_address(parsed.hostname or '')
        port = parsed.port
    except ValueError:
        raise ValueError('请使用 Mac 的局域网 IPv4 地址')
    private = any(address in network for network in (
        ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16')))
    if parsed.scheme != 'https' or not private or not port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('配对地址必须为 https://局域网IPv4:端口')
    return parsed


class PairStore:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock = clock
        self.lock = threading.RLock()
        self.devices_path = self.directory / 'devices.json'
        self.invite_path = self.directory / 'invite.json'

    @contextmanager
    def transaction(self):
        # Coordinate the local CLI and listener, not just listener threads.
        with self.lock:
            fd = os.open(self.directory / 'store.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    @staticmethod
    def _read(path):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {}

    @staticmethod
    def _save(path, value):
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.mywork-')
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def issue(self, origin, fingerprint):
        private_origin(origin)
        code = secrets.token_urlsafe(32)
        expires = int(self.clock()) + 300
        with self.transaction():
            self._save(self.invite_path, {'hash': self.digest(code), 'expiresAt': expires, 'used': False})
        return {'version': 1, 'origin': origin, 'fingerprint': fingerprint,
                'code': code, 'expiresAt': expires, 'name': 'MyWork Mac'}

    def claim(self, code, name):
        if not isinstance(code, str) or len(code) > 128:
            raise AgentViewsError('pair_invalid', '配对码无效', 403)
        with self.transaction():
            invite = self._read(self.invite_path)
            if invite.get('used') or self.clock() >= invite.get('expiresAt', 0) or not hmac.compare_digest(invite.get('hash', ''), self.digest(code)):
                raise AgentViewsError('pair_expired', '二维码已失效或已使用，请在 Mac 重新生成', 403)
            identifier, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            devices = self._read(self.devices_path)
            if len(devices) >= 20:
                raise AgentViewsError('devices_full', '请先在 Mac 移除不再使用的设备', 409)
            # Consume before issuing a credential; a lost response requires a fresh QR.
            invite['used'] = True
            self._save(self.invite_path, invite)
            devices[identifier] = {'hash': self.digest(token), 'name': str(name or 'Android')[:80],
                                   'pairedAt': int(self.clock())}
            self._save(self.devices_path, devices)
            return {'deviceId': identifier, 'token': token}

    def authorized(self, token):
        if not token or len(token) > 128:
            return False
        digest = self.digest(token)
        with self.transaction():
            return any(hmac.compare_digest(value.get('hash', ''), digest)
                       for value in self._read(self.devices_path).values())

    def revoke(self, identifier):
        with self.transaction():
            devices = self._read(self.devices_path)
            if identifier not in devices:
                raise ValueError('找不到该设备')
            del devices[identifier]
            self._save(self.devices_path, devices)


def certificate(directory):
    with PairStore(directory).transaction():
        return _certificate_locked(directory)


def _certificate_locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert, key = directory / 'server.pem', directory / 'server-key.pem'
    if not cert.exists() or not key.exists():
        if cert.exists() or key.exists():
            raise ValueError('证书文件不完整，请恢复证书备份后再启动')
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', temporary + '/key.pem', '-out', temporary + '/cert.pem',
                            '-days', '3650', '-subj', '/CN=MyWork Local Bridge'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            os.chmod(temporary + '/key.pem', 0o600)
            os.replace(temporary + '/key.pem', key)
            os.replace(temporary + '/cert.pem', cert)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


def main():
    parser = argparse.ArgumentParser(description='MyWork 局域网配对管理（仅在 Mac 本地运行）')
    parser.add_argument('--directory', type=Path, default=Path.home() / '.agent-views' / 'wifi')
    parser.add_argument('--origin', help='例如 https://192.168.1.10:8766')
    parser.add_argument('--output', type=Path, help='一次性二维码 PNG 的保存位置')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--revoke', help='撤销设备 ID')
    args = parser.parse_args()
    store = PairStore(args.directory)
    if args.list:
        print(json.dumps([{'deviceId': key, 'name': value.get('name'), 'pairedAt': value.get('pairedAt')}
                          for key, value in store._read(store.devices_path).items()], ensure_ascii=False))
    elif args.revoke:
        store.revoke(args.revoke)
        print('设备已撤销')
    elif args.origin and args.output:
        import qrcode
        _, _, fingerprint = certificate(args.directory)
        payload = store.issue(args.origin, fingerprint)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # QR contains a short-lived credential: never print its payload to logs.
        with args.output.open('wb') as output:
            os.chmod(args.output, 0o600)
            qrcode.make(json.dumps(payload, separators=(',', ':'))).save(output)
        print(json.dumps({'qrImage': str(args.output), 'expiresAt': payload['expiresAt']}, ensure_ascii=False))
    else:
        parser.error('使用 --origin 与 --output 生成二维码，或 --list / --revoke 管理设备')


if __name__ == '__main__':
    main()
