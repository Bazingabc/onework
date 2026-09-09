package com.oneripple.agentviews;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.widget.*;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;
import org.json.JSONObject;

public final class PairingActivity extends Activity {
    private TextView status;
    private Button scan, paste, usb;
    private boolean busy;
    private final java.util.concurrent.ExecutorService io = java.util.concurrent.Executors.newSingleThreadExecutor();

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ConnectionSettings.initialize(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (32 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundColor(AgentTheme.BACKGROUND);
        TextView title = new TextView(this);
        title.setText("连接 Mac"); title.setTextSize(26); title.setTextColor(AgentTheme.TEXT); root.addView(title);
        status = new TextView(this); status.setTextSize(17); status.setTextColor(AgentTheme.TEXT);
        status.setPadding(0, pad, 0, pad);
        status.setText(ConnectionSettings.label() + "\n\nMac 与平板连接同一局域网，在 Mac 生成配对二维码后扫描。配对有效期 5 分钟。");
        root.addView(status);
        scan = OneWorkUi.button(this, "扫描 Mac 配对码", true);
        scan.setOnClickListener(v -> new IntentIntegrator(this).setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                .setPrompt("扫描 Mac 上的 OneWork 配对码").setBeepEnabled(false).setOrientationLocked(false).initiateScan());
        root.addView(scan);
        paste = OneWorkUi.button(this, "粘贴配对内容", false);
        paste.setOnClickListener(v -> {
            EditText text = new EditText(this);
            text.setHint("OneWork 配对二维码内容");
            text.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE | android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
            new AlertDialog.Builder(this).setTitle("手动配对").setView(text)
                    .setPositiveButton("连接", (dialog, which) -> pair(text.getText().toString()))
                    .setNegativeButton("取消", null).show();
        });
        root.addView(paste);
        usb = OneWorkUi.button(this, "切换到 USB", false);
        usb.setOnClickListener(v -> { ConnectionSettings.useUsb(); setResult(RESULT_OK); finish(); });
        root.addView(usb);
        Button back = OneWorkUi.button(this, "返回 OneWork", false); back.setOnClickListener(v -> finish()); root.addView(back);
        setContentView(root);
        try {
            JSONObject pending = ConnectionSettings.pendingOperation("pairing");
            if (pending != null) pair(pending.getString("qr"));
        } catch (Exception ignored) { status.setText("未完成配对无法恢复，请重新扫码"); }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        IntentResult scanned = IntentIntegrator.parseActivityResult(request, result, data);
        if (scanned != null && scanned.getContents() != null) pair(scanned.getContents());
    }

    private void pair(String raw) {
        if (busy) return;
        final JSONObject value;
        final ConnectionSettings.Profile profile;
        try {
            if (raw.length() > 4096) throw new IllegalArgumentException();
            value = new JSONObject(raw);
            JSONObject saved = ConnectionSettings.pendingOperation("pairing");
            JSONObject validation = new JSONObject(raw);
            if (saved != null && raw.equals(saved.optString("qr")) && saved.has("requestId"))
                validation.put("expiresAt", saved.optLong("expiresAt"));
            profile = ConnectionSettings.fromQr(validation);
            if (!value.getString("code").matches("[A-Za-z0-9_-]{32,128}")) throw new IllegalArgumentException();
        } catch (Exception e) { status.setText("无效或已过期的配对码，请在 Mac 重新生成"); return; }
        busy = true; scan.setEnabled(false); paste.setEnabled(false); usb.setEnabled(false);
        status.setText("正在验证 Mac 并建立加密连接…");
        io.execute(() -> {
            try {
                JSONObject saved = ConnectionSettings.pendingOperation("pairing");
                String token;
                if (saved != null && raw.equals(saved.optString("qr"))) token=saved.getString("token");
                else {
                    byte[] secret = new byte[32]; new java.security.SecureRandom().nextBytes(secret);
                    token = android.util.Base64.encodeToString(secret, android.util.Base64.URL_SAFE | android.util.Base64.NO_WRAP | android.util.Base64.NO_PADDING);
                    ConnectionSettings.saveOperation("pairing",new JSONObject().put("qr",raw).put("token",token));
                }
                JSONObject body = new JSONObject().put("code", value.getString("code")).put("name", android.os.Build.MODEL).put("clientToken",token).put("device",DeviceMetadata.get());
                JSONObject result;
                if (saved != null && raw.equals(saved.optString("qr")) && saved.has("requestId")) result=saved;
                else {
                    result = ApiClient.requestWith(profile, "POST", "/api/pair/begin", body);
                    result.put("qr",raw).put("token",token).put("expiresAt",System.currentTimeMillis()/1000+295);
                    ConnectionSettings.saveOperation("pairing",result);
                }
                runOnUiThread(() -> status.setText("已找到 Mac。请在 Mac 的 OneWork「设备」中确认这台平板的权限。"));
                JSONObject polling = new JSONObject().put("requestId",result.getString("requestId")).put("clientToken",token);
                long deadline = android.os.SystemClock.elapsedRealtime() + 300_000;
                boolean approved = false;
                while (android.os.SystemClock.elapsedRealtime() < deadline && !Thread.currentThread().isInterrupted()) {
                    JSONObject check;
                    try { check=ApiClient.requestWith(profile,"POST","/api/pair/status",polling); }
                    catch (java.net.SocketTimeoutException | java.net.ConnectException e) { Thread.sleep(1500); continue; }
                    if (check.optString("state").equals("approved")) { approved=true; break; }
                    if (check.optString("state").equals("denied")) throw new java.io.IOException("Mac 已拒绝本次配对");
                    Thread.sleep(1500);
                }
                if (!approved) throw new java.io.IOException("等待 Mac 确认超时，请重新生成二维码");
                ConnectionSettings.Profile bound = new ConnectionSettings.Profile(profile.origin, profile.fingerprint, token);
                runOnUiThread(() -> {
                    if (isFinishing() || isDestroyed()) return;
                    try {
                        ConnectionSettings.save(bound);
                        ConnectionSettings.finishOperation("pairing");
                        setResult(RESULT_OK); Toast.makeText(this, "已连接 Mac · Wi-Fi", Toast.LENGTH_LONG).show(); finish();
                    } catch (Exception e) { showFailure(e); }
                });
            } catch (Exception e) {
                runOnUiThread(() -> showFailure(e));
            }
        });
    }

    private void showFailure(Exception error) {
        if (isFinishing() || isDestroyed()) return;
        busy = false; scan.setEnabled(true); paste.setEnabled(true); usb.setEnabled(true);
        String reason = error instanceof javax.net.ssl.SSLException ? "Mac 证书校验失败，请从 Mac 生成新码；不要忽略证书警告"
                : error instanceof java.net.SocketTimeoutException ? "连接超时，请检查同一局域网、Mac 服务和防火墙"
                : error instanceof java.net.ConnectException ? "无法连接 Mac，请确认服务已启动且网络允许设备互访"
                : error.getMessage();
        status.setText("连接未完成\n" + (reason == null ? error.getClass().getSimpleName() : reason));
    }

    @Override protected void onDestroy() { io.shutdownNow(); super.onDestroy(); }
}
