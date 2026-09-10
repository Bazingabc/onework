package com.one.onework;

import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

final class ApiClient {
    static final String BASE_URL = "http://127.0.0.1:8765";
    private static final int CONNECT_TIMEOUT_MS = 2_500;
    private static final int READ_TIMEOUT_MS = 10_000;

    static final class ApiException extends IOException {
        final int status;
        final String code;

        ApiException(int status, String code, String message) {
            super(message);
            this.status = status;
            this.code = code;
        }
    }

    private static long metadataAttempt;
    private static ConnectionSettings.Profile metadataProfile;
    private static final java.util.concurrent.ExecutorService METADATA_IO=java.util.concurrent.Executors.newSingleThreadExecutor(r -> {
        Thread thread=new Thread(r,"onework-device-metadata"); thread.setDaemon(true); return thread;
    });
    JSONObject health() throws Exception {
        ConnectionSettings.Profile profile=boundConnection();
        synchronized (ApiClient.class) {
            if (!profile.fingerprint.isEmpty() && (metadataProfile!=profile || android.os.SystemClock.elapsedRealtime()-metadataAttempt>60_000)) {
                metadataAttempt=android.os.SystemClock.elapsedRealtime(); metadataProfile=profile;
                METADATA_IO.execute(() -> {
                    if (profile != ConnectionSettings.snapshot()) return;
                    try { requestWith(profile,"POST","/api/device/profile",new JSONObject().put("device",DeviceMetadata.get())); }
                    catch (Exception ignored) { /* Noncritical display metadata must never block business IO. */ }
                });
            }
        }
        return request("GET", "/api/health", null);
    }

    JSONObject workTasks(boolean force) throws Exception {
        return request(force ? "POST" : "GET", "/api/work/tasks" + (force ? "/refresh" : ""),
                force ? new JSONObject() : null);
    }

    JSONObject completeTask(String id, String revision) throws Exception {
        return action("complete", id, revision, new JSONObject());
    }

    JSONObject verifyTask(String id) throws Exception {
        return request("POST", "/api/work/tasks/" + Uri.encode(id) + "/verify", new JSONObject());
    }

    JSONObject workUsage() throws Exception {
        return request("GET", "/api/work/usage", null);
    }

    static final class SessionSnapshot {
        final List<SessionItem> items;
        final boolean stale;
        final String message;
        SessionSnapshot(List<SessionItem> items, boolean stale, String message) {
            this.items = items; this.stale = stale; this.message = message;
        }
    }

    SessionSnapshot sessionsSnapshot() throws Exception {
        JSONObject payload = request("GET", "/api/sessions", null);
        JSONArray values = payload.optJSONArray("sessions");
        List<SessionItem> result = new ArrayList<>();
        for (int index = 0; values != null && index < values.length(); index++) {
            JSONObject value = values.optJSONObject(index);
            if (value != null) result.add(new SessionItem(value));
        }
        boolean stale = payload.optBoolean("stale");
        JSONObject snapshot = payload.optJSONObject("snapshot");
        String message = stale ? payload.optString("error", "列表快照已过期，正在同步")
                : snapshot != null && snapshot.optBoolean("refreshing") ? "后台更新中 · 展示最近快照" : "";
        return new SessionSnapshot(result, stale, message);
    }

    JSONObject session(String threadId) throws Exception {
        JSONObject payload = request(
                "GET", "/api/sessions/" + Uri.encode(threadId), null);
        JSONObject session = payload.optJSONObject("session");
        if (session == null) {
            JSONObject metadata = payload.optJSONObject("snapshot");
            boolean loading = metadata != null && "loading".equals(metadata.optString("state"));
            throw new ApiException(loading ? 202 : 503, loading ? "snapshot_loading" : "snapshot_unavailable",
                    payload.optString("error", "正在读取任务详情，稍后自动更新"));
        }
        session.put("_revision", payload.optString("revision"));
        JSONObject snapshot = payload.optJSONObject("snapshot");
        session.put("_stale", payload.optBoolean("stale"));
        session.put("_snapshotAgeMs", snapshot == null ? 0 : snapshot.optLong("ageMs", -1));
        session.put("_snapshotReceivedMs", android.os.SystemClock.elapsedRealtime());
        return session;
    }

    SessionItem createSession() throws Exception {
        JSONObject health = request("GET", "/api/product/status", null);
        JSONObject payload = action("create", "new", health.getString("serverEpoch"), new JSONObject());
        JSONObject session = payload.optJSONObject("session");
        if (session == null) throw new IOException("新建响应缺少 session");
        return new SessionItem(session);
    }

    JSONObject sendText(
            String threadId,
            String text,
            String expectedMessageId,
            long expectedUpdatedAt, String revision) throws Exception {
        JSONObject body = new JSONObject();
        body.put("text", text);
        if (expectedMessageId != null && !expectedMessageId.isEmpty()) {
            body.put("expectedMessageId", expectedMessageId);
        }
        if (expectedUpdatedAt > 0L) body.put("expectedUpdatedAt", expectedUpdatedAt);
        return action("message", threadId, revision, body);
    }

    JSONObject dismissAttention(String threadId, String messageId, String revision) throws Exception {
        JSONObject body = new JSONObject();
        body.put("messageId", messageId);
        return action("dismiss", threadId, revision, body);
    }

    JSONObject sendAnswers(String threadId, Map<String, String> answers, String revision) throws Exception {
        JSONObject encodedAnswers = new JSONObject();
        for (Map.Entry<String, String> entry : answers.entrySet()) {
            encodedAnswers.put(entry.getKey(), entry.getValue());
        }
        JSONObject body = new JSONObject();
        body.put("answers", encodedAnswers);
        return action("message", threadId, revision, body);
    }

    JSONObject respondApproval(String threadId, String decision, String revision) throws Exception {
        JSONObject body = new JSONObject();
        body.put("decision", decision);
        return action("approval", threadId, revision, body);
    }

    private JSONObject action(String kind, String target, String revision, JSONObject payload) throws Exception {
        if (revision == null || revision.isEmpty()) throw new IOException("请先刷新内容；Mac 与平板需同时升级");
        JSONObject body = new JSONObject().put("kind",kind).put("target",target)
                .put("expectedRevision",revision).put("payload",payload);
        String operation = ConnectionSettings.operationId(boundConnection().fingerprint + boundConnection().token + kind + target);
        JSONObject previous = ConnectionSettings.pendingOperation(operation);
        if (previous != null) {
            JSONObject record;
            try { record = request("GET", "/api/v2/operations/" + operation, null); }
            catch (ApiException e) {
                if (e.status != 404) throw e;
                // The original request may not have arrived. Only its exact body can be retried.
                record = null;
            }
            if (record != null) {
                if (record.optString("state").equals("succeeded")) {
                    ConnectionSettings.finishOperation(operation);
                    throw new IOException("上次操作已成功，未重复发送。请刷新后核实，清除已发送内容。");
                }
                if (record.optString("state").equals("acknowledged") || record.optString("state").equals("rejected")) {
                    ConnectionSettings.finishOperation(operation);
                    throw new IOException("上次操作已核实或未执行。请查看最新内容，再决定是否发送当前输入。");
                }
                throw new IOException("上次操作结果待确认，未发送新内容。请在 Mac 诊断页核实。");
            }
            if (!previous.optJSONObject("payload").toString().equals(payload.toString()))
                throw new IOException("上次提交尚未确认，当前输入已改变，未发送任何内容。请先恢复上次输入核实；当前草稿保留。");
            body = previous;
        }
        body.put("operationId", operation);
        ConnectionSettings.saveOperation(operation, body);
        try {
            JSONObject result = request("POST", "/api/v2/actions", body);
            ConnectionSettings.finishOperation(operation);
            return result;
        } catch (ApiException e) {
            if (!e.code.equals("operation_unknown") && e.status < 500) ConnectionSettings.finishOperation(operation);
            throw e;
        } catch (Exception e) {
            throw new IOException("提交结果待确认，请勿重复提交。可恢复连接后核实，或到 Mac 查看操作记录。");
        }
    }

    private ConnectionSettings.Profile boundConnection;

    private synchronized ConnectionSettings.Profile boundConnection() {
        if (boundConnection == null) boundConnection = ConnectionSettings.snapshot();
        return boundConnection;
    }

    private JSONObject request(String method, String path, JSONObject body) throws Exception {
        ConnectionSettings.Profile profile = boundConnection();
        if (profile != ConnectionSettings.snapshot()) throw new IOException("连接已切换，请等待页面刷新");
        JSONObject result = requestWith(profile, method, path, body);
        if (profile != ConnectionSettings.snapshot()) throw new IOException("已忽略旧连接的响应");
        return result;
    }

    static JSONObject requestWith(ConnectionSettings.Profile profile, String method, String path, JSONObject body) throws Exception {
        HttpURLConnection connection = ConnectionSettings.open(profile, path);
        try {
            connection.setRequestMethod(method);
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(path.startsWith("/api/work/") || (body != null && "complete".equals(body.optString("kind"))) ? 180_000 : READ_TIMEOUT_MS);
            connection.setRequestProperty("Accept", "application/json");
            connection.setUseCaches(false);
            if (body != null) {
                byte[] encoded = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setRequestProperty("X-Agent-Views-Client", "android");
                connection.setFixedLengthStreamingMode(encoded.length);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(encoded);
                }
            }
            int status = connection.getResponseCode();
            InputStream input = status >= 200 && status < 300
                    ? connection.getInputStream()
                    : connection.getErrorStream();
            String text = input == null ? "{}" : readFully(input);
            JSONObject payload = text.isEmpty() ? new JSONObject() : new JSONObject(text);
            if (status < 200 || status >= 300) {
                JSONObject error = payload.optJSONObject("error");
                String code = error == null ? "http_error" : error.optString("code", "http_error");
                String message = error == null
                        ? "Bridge 请求失败（" + status + "）"
                        : error.optString("message", "Bridge 请求失败");
                throw new ApiException(status, code, message);
            }
            return payload;
        } finally {
            connection.disconnect();
        }
    }

    private static String readFully(InputStream input) throws IOException {
        try (InputStream source = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4_096];
            int count;
            while ((count = source.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }
}
