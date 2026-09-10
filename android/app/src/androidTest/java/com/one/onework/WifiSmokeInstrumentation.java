package com.one.onework;

import android.app.Instrumentation;
import android.os.Bundle;
import org.json.JSONObject;

/** Read-only real-device protocol/TLS checks; never sends business messages. */
public final class WifiSmokeInstrumentation extends Instrumentation {
    private long slowestSnapshotMs;
    private int snapshotRequests;

    private JSONObject snapshot(ConnectionSettings.Profile profile, String path) throws Exception {
        long started = android.os.SystemClock.elapsedRealtime();
        JSONObject value = ApiClient.requestWith(profile, "GET", path, null);
        long elapsed = android.os.SystemClock.elapsedRealtime() - started;
        slowestSnapshotMs = Math.max(slowestSnapshotMs, elapsed);
        snapshotRequests++;
        if (elapsed >= 2_000) throw new AssertionError("Snapshot request blocked for " + elapsed + " ms");
        if (!value.has("snapshot")) throw new AssertionError("Snapshot metadata missing; upgrade Mac");
        return value;
    }

    private JSONObject awaitFresh(ConnectionSettings.Profile profile, String path) throws Exception {
        long deadline = android.os.SystemClock.elapsedRealtime() + 45_000;
        do {
            JSONObject value = snapshot(profile, path);
            if (!value.optBoolean("stale")) return value;
            Thread.sleep(500);
        } while (android.os.SystemClock.elapsedRealtime() < deadline);
        throw new AssertionError("Background snapshot did not become fresh within 45 s");
    }
    @Override public void onCreate(Bundle args) { super.onCreate(args); start(); }
    @Override public void onStart() {
        Bundle result=new Bundle();
        String stage="load encrypted profile";
        try {
            ConnectionSettings.initialize(getTargetContext());
            ConnectionSettings.Profile profile=ConnectionSettings.snapshot();
            if (profile.fingerprint.isEmpty()) throw new AssertionError("Wi-Fi pairing required");
            stage="explicit IPv4 discovery endpoint on dual-stack Mac";
            android.net.nsd.NsdServiceInfo discovered=new android.net.nsd.NsdServiceInfo();
            discovered.setHost(java.net.InetAddress.getByName("::1"));
            discovered.setPort(8766); discovered.setAttribute("origin",profile.origin);
            if (!profile.origin.equals(MacDiscovery.origin(discovered))) throw new AssertionError("Explicit endpoint ignored");
            stage="pinned TLS and authenticated protocol";
            JSONObject status=ApiClient.requestWith(profile,"GET","/api/product/status",null);
            if (status.getJSONObject("protocol").getInt("major")!=2) throw new AssertionError("Protocol must be v2");
            stage="session read";
            JSONObject sessions=awaitFresh(profile,"/api/sessions");
            if (!sessions.has("sessions")) throw new AssertionError("Session payload missing");
            for (int i=0; i<8; i++) {
                snapshot(profile,"/api/sessions");
                Thread.sleep(500);
            }
            if (sessions.getJSONArray("sessions").length()>0) {
                stage="on-demand fresh detail snapshot";
                String id=sessions.getJSONArray("sessions").getJSONObject(0).getString("id");
                JSONObject detail=awaitFresh(profile,"/api/sessions/"+android.net.Uri.encode(id));
                if (detail.optJSONObject("session")==null || detail.optString("revision").isEmpty())
                    throw new AssertionError("Fresh detail must include an actionable revision");
            }
            stage="revoked/unrecognized credential rejection";
            try {
                ApiClient.requestWith(new ConnectionSettings.Profile(profile.origin,profile.fingerprint,"invalid-token"),"GET","/api/product/status",null);
                throw new AssertionError("Invalid credential accepted");
            } catch (ApiClient.ApiException expected) {
                if (expected.status!=401 && expected.status!=403) throw expected;
            }
            stage="wrong certificate pin rejection";
            try {
                ApiClient.requestWith(new ConnectionSettings.Profile(profile.origin,"0".repeat(64),profile.token),"GET","/api/product/status",null);
                throw new AssertionError("Wrong certificate pin accepted");
            } catch (javax.net.ssl.SSLException expected) { }
            result.putString("stream","PASS: Wi-Fi profile retained, explicit IPv4 discovery endpoint, pinned TLS, protocol v2, background list/detail snapshots, invalid-token rejection, wrong-pin rejection. Snapshot requests="+snapshotRequests+", max="+slowestSnapshotMs+" ms. No business writes.\n");
            finish(-1,result);
        } catch (Throwable error) {
            result.putString("stream","FAIL: "+stage+"\n"+android.util.Log.getStackTraceString(error));
            finish(1,result);
        }
    }
}
