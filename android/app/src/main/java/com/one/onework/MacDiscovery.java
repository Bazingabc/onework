package com.one.onework;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import org.json.JSONObject;

/** Discovery is only an address hint; pinned TLS and device auth remain mandatory. */
final class MacDiscovery implements NsdManager.DiscoveryListener {
    private final NsdManager manager;
    private final java.util.concurrent.ExecutorService io=java.util.concurrent.Executors.newSingleThreadExecutor();
    private volatile boolean closed;
    private final java.util.concurrent.atomic.AtomicBoolean resolving=new java.util.concurrent.atomic.AtomicBoolean();
    MacDiscovery(Context context) { manager=(NsdManager)context.getSystemService(Context.NSD_SERVICE); }
    void start() {
        if (!ConnectionSettings.wifi()) return;
        try { manager.discoverServices("_onework._tcp.",NsdManager.PROTOCOL_DNS_SD,this); }
        catch (RuntimeException ignored) { /* Explicit QR remains available. */ }
    }
    void close() {
        closed=true;
        try { manager.stopServiceDiscovery(this); } catch (RuntimeException ignored) {}
        io.shutdownNow();
    }
    @Override public void onServiceFound(NsdServiceInfo service) {
        if (closed || !resolving.compareAndSet(false,true)) return;
        manager.resolveService(service,new NsdManager.ResolveListener() {
            @Override public void onResolveFailed(NsdServiceInfo value,int error) { resolving.set(false); }
            @Override public void onServiceResolved(NsdServiceInfo value) {
                if (closed) { resolving.set(false); return; }
                io.execute(() -> {
                    try {
                        ConnectionSettings.Profile profile=ConnectionSettings.snapshot();
                        byte[] pin=value.getAttributes().get("fingerprint");
                        if (pin==null || !profile.fingerprint.equals(new String(pin,java.nio.charset.StandardCharsets.UTF_8))) return;
                        String origin=origin(value);
                        ConnectionSettings.fromQr(new JSONObject().put("version",2).put("expiresAt",System.currentTimeMillis()/1000+60)
                                .put("origin",origin).put("fingerprint",profile.fingerprint));
                        ConnectionSettings.Profile candidate=new ConnectionSettings.Profile(origin,profile.fingerprint,profile.token);
                        JSONObject status=ApiClient.requestWith(candidate,"GET","/api/product/status",null);
                        if ("ready".equals(status.optString("bridge")) && !closed && profile==ConnectionSettings.snapshot())
                            ConnectionSettings.discovered(profile,origin);
                    } catch (Exception ignored) { /* Never relax TLS or replace credentials. */ }
                    finally { resolving.set(false); }
                });
            }
        });
    }
    @Override public void onDiscoveryStarted(String type) {}
    static String origin(NsdServiceInfo value) {
        // TXT is only a hint. The caller still validates private IPv4, pins TLS,
        // authenticates, and checks the profile has not changed before saving.
        byte[] explicit=value.getAttributes().get("origin");
        if (explicit!=null && explicit.length>0 && explicit.length<=200)
            return new String(explicit,java.nio.charset.StandardCharsets.UTF_8);
        return "https://"+value.getHost().getHostAddress()+":"+value.getPort();
    }
    @Override public void onDiscoveryStopped(String type) {}
    @Override public void onStartDiscoveryFailed(String type,int error) {}
    @Override public void onStopDiscoveryFailed(String type,int error) {}
    @Override public void onServiceLost(NsdServiceInfo service) {}
}
