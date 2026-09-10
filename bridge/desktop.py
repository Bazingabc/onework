"""Local-only native-app controller. JSON over process pipes, never exposed on LAN."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import sqlite3
import urllib.request
from .devices import DeviceStore
from .pairing import PairStore, certificate, private_origin
from .network import private_address

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Source checkout has the compatibility entry point; packaged runtime has the
# real agent_views package, whose parent is already on PYTHONPATH.
ROOT = PACKAGE_ROOT if (PACKAGE_ROOT / 'agent_views/__init__.py').is_file() else PACKAGE_ROOT.parent
DATA_DIR = Path.home() / '.agent-views'


def read(path):
    try: return json.loads(path.read_text())
    except (FileNotFoundError, ValueError): return {}


def config():
    value=read(DATA_DIR/'desktop.json')
    value.setdefault('codex', shutil.which('codex') or '')
    value.setdefault('lark', shutil.which('lark-cli') or '')
    value.setdefault('workspace', str(Path.home()))
    return value


def address():
    return private_address()


def probe(executable):
    if not executable or not os.path.isfile(executable): return {'state':'not_installed','version':None}
    try:
        result=subprocess.run([executable,'--version'],capture_output=True,text=True,timeout=5)
        return {'state':'found' if result.returncode==0 else 'error','version':(result.stdout or result.stderr).strip()[:100]}
    except (OSError,subprocess.TimeoutExpired): return {'state':'error','version':None}


def service_status():
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8765/api/product/status',timeout=2) as response:
            return json.load(response)
    except Exception: return {'bridge':'offline'}


def owned_process():
    saved=read(DATA_DIR/'desktop-process.json')
    if not saved.get('pid'): return None
    result=subprocess.run(['/bin/ps','-p',str(saved['pid']),'-o','lstart=,command='],capture_output=True,text=True,timeout=2)
    return saved['pid'] if result.stdout.strip()==saved.get('signature') and '-m agent_views.bridge ' in result.stdout else None


def status():
    store=DeviceStore(DATA_DIR/'wifi')
    service=service_status()
    pin=certificate(DATA_DIR/'wifi')[2] if service.get('bridge')=='ready' else None
    return {'service':service,'fingerprint':pin,'devices':store.devices(),'pending':store.pending(),
            'configuration':config(),'managed':bool(owned_process()),'address':address(),
            'auth':read(DATA_DIR/'lark-auth-state.json')}


def start():
    with PairStore(DATA_DIR/'controller').transaction():
        return _start_locked()


def _start_locked():
    if service_status()['bridge']!='offline': return {'message':'服务已运行'}
    if owned_process(): return {'message':'服务正在启动，请稍候'}
    settings=config()
    host=address()
    if not host: raise ValueError('未找到私网 IPv4。请连接可互访的局域网后重试')
    if not settings['codex']: raise ValueError('请先在数据源中选择 Codex CLI')
    command=[sys.executable,'-m','agent_views.bridge','--workspace',settings['workspace'],
             '--codex',settings['codex'],'--lan-host',host,'--follow-network']
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', ONEWORK_LARK_CLI=settings['lark'] or 'lark-cli',
             PYTHONPATH=str(ROOT), LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
    with (DATA_DIR/'desktop-service.log').open('ab') as log:
        os.chmod(DATA_DIR/'desktop-service.log',0o600)
        child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
    signature=subprocess.run(['/bin/ps','-p',str(child.pid),'-o','lstart=,command='],capture_output=True,text=True,timeout=2).stdout.strip()
    PairStore._save(DATA_DIR/'desktop-process.json', {'pid':child.pid,'signature':signature})
    return {'message':'正在启动'}


def stop():
    pid=owned_process()
    if not pid: raise ValueError('当前服务不是本应用启动，未终止外部进程')
    os.kill(pid,signal.SIGINT)
    return {'message':'已请求停止服务；未操作其他 Codex 进程'}


def auth_check():
    executable=config()['lark']
    if not executable: raise ValueError('请先选择飞书 CLI')
    result=subprocess.run([executable,'auth','status','--json','--verify'],capture_output=True,text=True,timeout=20)
    try: data=json.loads(result.stdout or result.stderr)
    except ValueError: raise ValueError('飞书 CLI 未返回可识别的授权状态，请检查版本')
    source=data.get('data',data)
    user=(source.get('identities') or {}).get('user') or {}
    safe={'state':'checked','identity':source.get('identity'),'verified':source.get('verified'),
          'userName':user.get('userName'),'status':user.get('status'),'tokenStatus':user.get('tokenStatus'),
          'error':(data.get('error') or {}).get('subtype')}
    PairStore._save(DATA_DIR/'lark-auth-state.json',safe)
    return safe


def auth_start():
    executable=config()['lark']
    if not executable: raise ValueError('请先选择飞书 CLI')
    result=subprocess.run([executable,'auth','login','--domain','task','--no-wait','--json'],capture_output=True,text=True,timeout=30)
    data=json.loads(result.stdout or result.stderr)
    if result.returncode: raise ValueError('飞书授权发起失败，请检查应用配置或 CLI 版本')
    source=data.get('data',data)
    url=source.get('verification_url') or source.get('verification_uri_complete')
    code=source.get('device_code')
    if not url or not code: raise ValueError('飞书 CLI 未返回可识别的授权链接')
    PairStore._save(DATA_DIR/'lark-auth-pending.json',{'code':code,'expires':time.time()+min(int(source.get('expires_in',300)),600)})
    qr=DATA_DIR/'lark-auth.png'
    subprocess.run([executable,'auth','qrcode',url,'--output',qr.name],cwd=DATA_DIR,capture_output=True,text=True,timeout=10,check=True)
    qr.chmod(0o600)
    return {'url':url,'qr':str(qr),'message':'请在飞书授权页面确认，完成后点击“我已完成授权”'}


def auth_finish():
    pending=read(DATA_DIR/'lark-auth-pending.json')
    if pending.get('expires',0)<=time.time(): raise ValueError('授权流程已过期，请重新发起')
    result=subprocess.run([config()['lark'],'auth','login','--device-code',pending['code'],'--json'],capture_output=True,text=True,timeout=30)
    if result.returncode: raise ValueError('授权尚未完成，或 CLI 未能确认，请重试核实')
    PairStore._save(DATA_DIR/'lark-auth-pending.json',{})
    return auth_check()


def main():
    DATA_DIR.mkdir(mode=0o700,parents=True,exist_ok=True)
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['status','start','stop','pair','approve','replace','revoke','role','configure','probe','auth-check','auth-start','auth-finish','acknowledge'])
    parser.add_argument('--id'); parser.add_argument('--role',choices=['view','control','denied'])
    parser.add_argument('--old-id')
    parser.add_argument('--expected-role', choices=['view','control'])
    parser.add_argument('--confirm-online', action='store_true')
    parser.add_argument('--key',choices=['codex','lark','workspace']); parser.add_argument('--value')
    args=parser.parse_args()
    try:
        store=DeviceStore(DATA_DIR/'wifi')
        if args.action=='status': result=status()
        elif args.action=='start': result=start()
        elif args.action=='stop': result=stop()
        elif args.action=='pair':
            host=address()
            if not host or service_status()['bridge']!='ready': raise ValueError('请先启动 Mac 服务再添加设备')
            if service_status().get('lanUrl') != 'https://'+host+':8766':
                raise ValueError('局域网连接正在恢复，请稍后重新生成配对码；无需重新配对已有设备')
            _,_,pin=certificate(DATA_DIR/'wifi')
            result=store.issue('https://'+host+':8766',pin)
        elif args.action=='approve': store.approve(args.id,args.role); result={'message':'已处理设备申请'}
        elif args.action=='replace': result=store.replace(args.id,args.old_id,args.expected_role,args.confirm_online)
        elif args.action=='revoke': store.revoke(args.id); result={'message':'设备访问已撤销'}
        elif args.action=='role': store.set_role(args.id,args.role); result={'message':'权限已更新'}
        elif args.action=='auth-check': result=auth_check()
        elif args.action=='auth-start': result=auth_start()
        elif args.action=='auth-finish': result=auth_finish()
        elif args.action=='acknowledge':
            with sqlite3.connect(DATA_DIR/'operations.sqlite3') as db:
                changed=db.execute("UPDATE operations SET state='acknowledged' WHERE id=? AND state='unknown'",(args.id,)).rowcount
            if not changed: raise ValueError('仅结果未知的操作可以人工确认')
            result={'message':'已记录人工核实，不会重新执行原操作；现在可以提交新的操作'}
        elif args.action=='probe': result={k:probe(v) for k,v in config().items() if k in ('codex','lark')}
        else:
            if not args.value or not args.key: raise ValueError('缺少配置')
            path=Path(args.value).expanduser().resolve()
            if args.key=='workspace':
                if not path.is_dir(): raise ValueError('请选择存在的目录')
            elif not path.is_file() or not os.access(path,os.X_OK): raise ValueError('请选择可执行 CLI 文件')
            value=config(); value[args.key]=str(path); PairStore._save(DATA_DIR/'desktop.json',value)
            result={'message':'已保存，下次启动服务生效'}
        print(json.dumps({'ok':True,'data':result},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:400]},ensure_ascii=False))
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
