"""Build an isolated, network-disabled copy of the real Android UI for README captures.

Requires an explicitly selected adb device and a configured Android build environment.
Only the temporary source copy is transformed; production sources are never modified.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PACKAGE = "com.oneripple.agentviews.docs"


def replace(path, old, new):
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"Capture seam changed: {path.name}: {old[:60]}")
    path.write_text(source.replace(old, new))


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--output", type=Path, default=HERE)
    args = parser.parse_args()
    adb = [args.adb, "-s", args.serial]
    installed = run(*adb, "shell", "pm", "list", "packages", PACKAGE, capture_output=True, text=True).stdout
    if f"package:{PACKAGE}" in installed or f"package:{PACKAGE}.test" in installed:
        raise SystemExit("Demo package already exists; inspect/remove it explicitly before capture.")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="onework-docs-") as temporary:
        android = Path(temporary) / "android"
        shutil.copytree(ROOT / "android", android, ignore=shutil.ignore_patterns("build", ".gradle", "local.properties"))
        java = android / "app/src/main/java/com/oneripple/agentviews"
        tests = android / "app/src/androidTest/java/com/oneripple/agentviews"
        replace(android / "app/build.gradle.kts", 'applicationId = "com.oneripple.agentviews"', f'applicationId = "{PACKAGE}"')
        replace(android / "app/build.gradle.kts", 'com.oneripple.agentviews.WifiSmokeInstrumentation', 'com.oneripple.agentviews.DocsCapture')
        # OS-enforced isolation, in addition to replacing the only API transport.
        replace(android / "app/src/main/AndroidManifest.xml", '    <uses-permission android:name="android.permission.INTERNET" />',
                '    <uses-permission android:name="android.permission.INTERNET" tools:node="remove" />')
        replace(android / "app/src/main/AndroidManifest.xml", '    <uses-permission android:name="android.permission.CAMERA" />',
                '    <uses-permission android:name="android.permission.CAMERA" tools:node="remove" />')
        replace(java / "ApiClient.java", 'static JSONObject requestWith(ConnectionSettings.Profile profile, String method, String path, JSONObject body) throws Exception {',
                'static JSONObject requestWith(ConnectionSettings.Profile profile, String method, String path, JSONObject body) throws Exception { return MockData.response(method, path); }\n'
                '    private static JSONObject unusedRealTransport(ConnectionSettings.Profile profile, String method, String path, JSONObject body) throws Exception {')
        replace(java / "MainActivity.java", '        macDiscovery=new MacDiscovery(this);\n        macDiscovery.start();', '        // Documentation capture: discovery disabled, including service initialization.')
        replace(java / "ConnectionSettings.java", 'static String label() {', 'static String label() { return "DEMO · 模拟连接"; }\n    static String unusedLabel() {')
        shutil.copy2(HERE / "MockData.java", java / "MockData.java")
        shutil.copy2(HERE / "DocsCapture.java", tests / "DocsCapture.java")
        run(str(android / "gradlew"), "--version", cwd=android)
        run(str(android / "gradlew"), "--no-daemon", "assembleDebug", "assembleDebugAndroidTest", cwd=android)
        app_installed = test_installed = False
        try:
            run(*adb, "install", str(android / "app/build/outputs/apk/debug/app-debug.apk"))
            app_installed = True
            run(*adb, "install", str(android / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"))
            test_installed = True
            with subprocess.Popen([*adb, "shell", "am", "instrument", "-w", f"{PACKAGE}.test/com.oneripple.agentviews.DocsCapture"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as capture:
                try:
                    # Some devices block an instrumentation process's background
                    # activity launch. Explicitly foreground this demo package only.
                    time.sleep(2)
                    run(*adb, "shell", "am", "start", "-W", "-n", f"{PACKAGE}/com.oneripple.agentviews.MainActivity", timeout=20)
                    output, _ = capture.communicate(timeout=45)
                    print(output)
                    if "Mock UI screenshots captured" not in output:
                        raise RuntimeError("Native capture did not report success")
                except BaseException:
                    capture.kill()
                    capture.communicate()
                    raise
            for name in ("android-home.png", "android-agents.png"):
                data = run(*adb, "exec-out", "run-as", PACKAGE, "cat", f"files/{name}", capture_output=True).stdout
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError(f"Invalid screenshot: {name}")
                (args.output / name).write_bytes(data)
        finally:
            if test_installed: run(*adb, "uninstall", f"{PACKAGE}.test")
            if app_installed: run(*adb, "uninstall", PACKAGE)


if __name__ == "__main__":
    main()
