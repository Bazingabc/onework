package com.one.onework;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.*;

/** One immutable snapshot per request. Credentials are encrypted by Android Keystore. */
final class ConnectionSettings {
    static final class Profile {
        final String origin, fingerprint, token;
        Profile(String origin, String fingerprint, String token) {
            this.origin = origin; this.fingerprint = fingerprint; this.token = token;
        }
    }
    private static volatile Profile current = new Profile("http://127.0.0.1:8765", "", "");
    private static volatile Profile discoveredProfile;
    private static volatile String discoveredOrigin;
    static synchronized void discovered(Profile profile,String origin) {
        if (profile==current) { discoveredOrigin=origin; discoveredProfile=profile; }
    }
    private static SharedPreferences prefs;
    private static final String KEY = "mywork-pairing";

    static synchronized void initialize(Context context) {
        if (prefs != null) return;
        prefs = context.getSharedPreferences("mywork-connection", Context.MODE_PRIVATE);
        String encrypted = prefs.getString("profile", "");
        if (encrypted.isEmpty()) return;
        try {
            String[] pieces = encrypted.split(":");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, secret(), new GCMParameterSpec(128, Base64.decode(pieces[0], Base64.NO_WRAP)));
            JSONObject value = new JSONObject(new String(cipher.doFinal(Base64.decode(pieces[1], Base64.NO_WRAP)), java.nio.charset.StandardCharsets.UTF_8));
            current = new Profile(value.getString("origin"), value.getString("fingerprint"), value.getString("token"));
        } catch (Exception e) {
            // Never silently route writes to a different Mac when saved pairing is unreadable.
            current = new Profile("", "", "");
        }
    }

    private static SecretKey secret() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(KEY)) return (SecretKey) store.getKey(KEY, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }

    static Profile snapshot() { return current; }
    static synchronized String operationId(String signature) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(signature.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        String key = "op-" + Base64.encodeToString(bytes, Base64.NO_WRAP);
        String identifier = prefs.getString(key, "");
        if (identifier.isEmpty()) {
            identifier = java.util.UUID.randomUUID().toString();
            if (!prefs.edit().putString(key, identifier).commit()) throw new java.io.IOException("无法保存操作记录，未发送");
        }
        return identifier;
    }
    static synchronized void finishOperation(String identifier) {
        SharedPreferences.Editor editor = prefs.edit();
        editor.remove("operation-body-" + identifier);
        for (java.util.Map.Entry<String,?> entry : prefs.getAll().entrySet())
            if (entry.getKey().startsWith("op-") && identifier.equals(entry.getValue())) editor.remove(entry.getKey());
        editor.commit();
    }
    static synchronized JSONObject pendingOperation(String identifier) throws Exception {
        String stored = prefs.getString("operation-body-" + identifier, "");
        if (stored.isEmpty()) return null;
        String[] parts = stored.split(":");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, secret(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        return new JSONObject(new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), java.nio.charset.StandardCharsets.UTF_8));
    }
    static synchronized void saveOperation(String identifier, JSONObject body) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, secret());
        byte[] encrypted = cipher.doFinal(body.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        if (!prefs.edit().putString("operation-body-" + identifier, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" + Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit())
            throw new java.io.IOException("无法保存操作，未发送");
    }
    static boolean wifi() { return !current.origin.equals("http://127.0.0.1:8765"); }
    static String label() { return current.origin.isEmpty() ? "配对需恢复" : wifi() ? "Wi-Fi · " + current.origin.replace("https://", "") : "USB 连接"; }

    static synchronized void save(Profile profile) throws Exception {
        JSONObject value = new JSONObject().put("origin", profile.origin).put("fingerprint", profile.fingerprint).put("token", profile.token);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, secret());
        byte[] encrypted = cipher.doFinal(value.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        if (!prefs.edit().putString("profile", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
                + Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit()) throw new java.io.IOException("无法保存配对");
        current = profile;
    }

    static synchronized void useUsb() {
        if (!prefs.edit().remove("profile").commit()) return;
        current = new Profile("http://127.0.0.1:8765", "", "");
    }

    static Profile fromQr(JSONObject value) throws Exception {
        if (value.optInt("version") != 2 || value.optLong("expiresAt") <= System.currentTimeMillis() / 1000)
            throw new java.io.IOException("二维码已过期，请在 Mac 重新生成");
        String origin = value.getString("origin"), pin = value.getString("fingerprint");
        URL url = new URL(origin);
        String host = url.getHost();
        String[] octets = host.split("\\.");
        if (octets.length != 4) throw new java.io.IOException("配对地址无效");
        int[] ip = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!octets[i].matches("[0-9]{1,3}")) throw new java.io.IOException("配对地址无效");
            ip[i] = Integer.parseInt(octets[i]);
            if (ip[i] > 255) throw new java.io.IOException("配对地址无效");
        }
        boolean local = ip[0] == 10 || (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31) || (ip[0] == 192 && ip[1] == 168);
        if (!local || !url.getProtocol().equals("https") || url.getPort() < 1 || url.getPort() > 65535
                || url.getUserInfo() != null || !url.getFile().isEmpty() || url.getRef() != null || !pin.matches("[0-9a-f]{64}"))
            throw new java.io.IOException("配对地址或证书指纹无效");
        return new Profile(origin, pin, "");
    }

    static boolean matches(X509Certificate cert, String pin) throws Exception {
        cert.checkValidity();
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(cert.getEncoded());
        StringBuilder hex = new StringBuilder();
        for (byte b : digest) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return MessageDigest.isEqual(hex.toString().getBytes(java.nio.charset.StandardCharsets.US_ASCII), pin.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    }

    static HttpURLConnection open(Profile profile, String path) throws Exception {
        if (profile.origin.isEmpty()) throw new java.io.IOException("请重新扫码配对，或选择 USB 连接");
        String origin = profile==discoveredProfile ? discoveredOrigin : profile.origin;
        URL endpoint = new URL(origin + path);
        HttpURLConnection connection = (HttpURLConnection) endpoint.openConnection();
        connection.setInstanceFollowRedirects(false);
        if (!profile.fingerprint.isEmpty()) {
            X509TrustManager trust = new X509TrustManager() {
                public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                public void checkClientTrusted(X509Certificate[] chain, String auth) throws java.security.cert.CertificateException { throw new java.security.cert.CertificateException("client cert unsupported"); }
                public void checkServerTrusted(X509Certificate[] chain, String auth) throws java.security.cert.CertificateException {
                    try { if (chain.length > 0 && matches(chain[0], profile.fingerprint)) return; }
                    catch (Exception ignored) {}
                    throw new java.security.cert.CertificateException("Mac 证书不匹配，请重新配对");
                }
            };
            SSLContext tls = SSLContext.getInstance("TLS");
            tls.init(null, new TrustManager[]{trust}, null);
            HttpsURLConnection secure = (HttpsURLConnection) connection;
            secure.setSSLSocketFactory(tls.getSocketFactory());
            secure.setHostnameVerifier((host, session) -> {
                try { return host.equals(endpoint.getHost()) && matches((X509Certificate) session.getPeerCertificates()[0], profile.fingerprint); }
                catch (Exception e) { return false; }
            });
        } else if (!profile.origin.equals("http://127.0.0.1:8765")) {
            connection.disconnect();
            throw new java.io.IOException("未验证的连接地址");
        }
        if (!profile.token.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + profile.token);
        return connection;
    }
}
