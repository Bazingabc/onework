"""Build a relocatable, ad-hoc-signed local acceptance app (not notarized)."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--python-home', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(Path(__file__).with_name('sync_brand.py')), '--check'], check=True)
app = args.output.resolve() / 'OneWork.app'
if app.exists():
    raise SystemExit('Output already exists; choose a new output directory')
resources = app / 'Contents/Resources'
binary = app / 'Contents/MacOS'
binary.mkdir(parents=True)
resources.mkdir(parents=True)
shutil.copy2(Path(__file__).with_name('install.py'), resources/'install.py')
shutil.copytree(args.python_home.resolve(), resources/'python', symlinks=True,
                ignore=shutil.ignore_patterns('__pycache__', 'site-packages'))
runtime = resources/'runtime'
shutil.copytree(root/'bridge', runtime/'agent_views/bridge', ignore=shutil.ignore_patterns('__pycache__','tests'))
shutil.copy2(root/'__init__.py', runtime/'agent_views/__init__.py')
with (app/'Contents/Info.plist').open('wb') as output:
    plistlib.dump({'CFBundleExecutable':'OneWork','CFBundleIdentifier':'com.oneripple.onework.mac',
        'CFBundleName':'OneWork','CFBundleDisplayName':'OneWork','CFBundlePackageType':'APPL',
        'CFBundleShortVersionString':'0.3.2','CFBundleVersion':'7','LSMinimumSystemVersion':'13.0',
        'CFBundleIconFile':'OneWork.icns',
        'NSLocalNetworkUsageDescription':'查找同一局域网中的 OneWork 平板并建立加密连接。',
        'NSBonjourServices':['_onework._tcp.'],'NSHighResolutionCapable':True},output)
subprocess.run(['xcrun','swift','-module-cache-path','/private/tmp/onework-swift-cache',str(Path(__file__).with_name('render_icons.swift')),
    str(root/'android/app/src/main/res/drawable/ic_onework_mark.xml'),str(resources)],check=True)
subprocess.run(['/usr/bin/iconutil','-c','icns',str(resources/'OneWork.iconset'),'-o',str(resources/'OneWork.icns')],check=True)
subprocess.run(['xcrun','swiftc','-swift-version','5','-parse-as-library',str(Path(__file__).with_name('OneWork.swift')),
    '-o',str(binary/'OneWork'),'-framework','SwiftUI','-framework','AppKit','-framework','ServiceManagement',
    '-module-cache-path','/private/tmp/onework-swift-cache'],check=True)
subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
print(app)
