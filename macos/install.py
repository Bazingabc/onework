"""Local, explicit upgrades with preflight, atomic replacement and retained backups.

No downloads, process termination, credential migration, or automatic downgrades.
Default is a read-only plan. Quit the installed UI and stop its managed service
before --apply; the installer refuses to replace any bundle still in use.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import uuid

BUNDLE_ID = 'com.oneripple.onework.mac'
DEFAULT_TARGET = Path.home() / 'Applications' / 'OneWork.app'


def bundle_info(path):
    if path.is_symlink() or not path.is_dir() or path.suffix != '.app':
        raise ValueError('请选择真实的 OneWork.app 目录，不能使用符号链接')
    with (path / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'OneWork':
        raise ValueError('应用身份不匹配，未更改安装')
    for relative in ('MacOS/OneWork', 'Resources/python/bin/python3',
                     'Resources/runtime/agent_views/bridge/desktop.py'):
        if not (path / 'Contents' / relative).is_file():
            raise ValueError('应用缺少必要运行文件：' + relative)
    return {'version': str(info['CFBundleShortVersionString']), 'build': str(info['CFBundleVersion'])}


def verify_signature(path):
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                   check=True, capture_output=True, text=True)


def running_processes(path):
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,comm='], check=True, capture_output=True, text=True)
    prefix = str(path.resolve()) + '/'
    return [int(parts[0]) for line in result.stdout.splitlines()
            if len(parts := line.strip().split(maxsplit=1)) == 2 and parts[1].startswith(prefix)]


def prepare(source, target, *, verify=verify_signature, running=running_processes, rollback=False):
    # Never resolve away a symlink before rejecting it.
    source, target = source.expanduser().absolute(), target.expanduser().absolute()
    if target.name != 'OneWork.app' or target.is_symlink() or target.parent.resolve() != target.parent:
        raise ValueError('安装目标必须是非符号链接目录中的 OneWork.app')
    if source == target or target in source.parents or source in target.parents:
        raise ValueError('安装来源与目标不能相同或互相包含')
    incoming = bundle_info(source)
    verify(source)
    existing = bundle_info(target) if target.exists() else None
    if existing and not rollback and int(incoming['build']) < int(existing['build']):
        raise ValueError('拒绝静默降级；请使用明确的 --rollback')
    pids = running(target) if target.exists() else []
    return {'source': str(source), 'target': str(target), 'incoming': incoming,
            'existing': existing, 'runningPids': pids, 'rollback': rollback,
            'dataPolicy': '保留 ~/.agent-views，不读取或移动配对凭证'}


@contextmanager
def install_lock(parent):
    lock_path = parent / '.onework-install.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install(source, target, *, verify=verify_signature, running=running_processes, rollback=False):
    plan = prepare(source, target, verify=verify, running=running, rollback=rollback)
    target, source = Path(plan['target']), Path(plan['source'])
    target.parent.mkdir(parents=True, exist_ok=True)
    with install_lock(target.parent):
        plan = prepare(source, target, verify=verify, running=running, rollback=rollback)
        if plan['runningPids']:
            raise ValueError('请先停止 OneWork 服务并退出界面；未替换运行中的应用')
        backup_root = target.parent / '.onework-backups'
        if backup_root.is_symlink():
            raise ValueError('备份目录不能是符号链接')
        backup_root.mkdir(mode=0o700, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.onework-stage-', dir=target.parent))
        candidate = stage / 'OneWork.app'
        backup = None
        placed = False
        cleanup = True
        try:
            shutil.copytree(source, candidate, symlinks=True)
            bundle_info(candidate)
            verify(candidate)
            if running(target):
                raise ValueError('应用在准备期间重新启动；未替换，请退出后重试')
            if target.exists():
                label = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
                backup = backup_root / label
                backup.mkdir(mode=0o700)
                target.rename(backup / 'OneWork.app')
            candidate.rename(target)
            placed = True
            verify(target)
        except BaseException as failure:
            try:
                if placed:
                    target.rename(stage / 'failed.app')
                if backup and (backup / 'OneWork.app').exists():
                    (backup / 'OneWork.app').rename(target)
            except BaseException as recovery_failure:
                cleanup = False
                raise RuntimeError('安装失败：%s；自动恢复也失败：%s。未清理恢复现场。旧版备份：%s；暂存目录：%s。'
                                   '请保留这些目录，并在应用退出后使用 --rollback 恢复备份。'
                                   % (failure, recovery_failure, backup, stage)) from recovery_failure
            raise
        finally:
            # Only our exact mkdtemp staging directory, never the installation.
            if cleanup:
                shutil.rmtree(stage)
        return dict(plan, installed=True, backup=str(backup) if backup else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', type=Path)
    group.add_argument('--rollback', help='使用 .onework-backups 中的准确备份目录名')
    parser.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    parser.add_argument('--apply', action='store_true', help='明确执行安装；否则仅预检')
    args = parser.parse_args()
    source = args.source
    try:
        if args.rollback:
            if Path(args.rollback).name != args.rollback or args.rollback in ('.', '..'):
                raise ValueError('无效备份目录名')
            root = args.target.expanduser().absolute().parent / '.onework-backups'
            source = root / args.rollback / 'OneWork.app'
            if root.is_symlink() or source.parent.is_symlink():
                raise ValueError('不能从符号链接备份恢复')
        operation = install if args.apply else prepare
        print(json.dumps(operation(source, args.target, rollback=bool(args.rollback)), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
