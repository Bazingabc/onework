package com.one.onework;

import android.content.res.Configuration;
import android.content.res.Resources;
import android.os.Build;
import org.json.JSONObject;

/** Self-reported display metadata only, never hardware IDs or authorization evidence. */
final class DeviceMetadata {
    private static JSONObject cached;
    static synchronized JSONObject get() throws Exception {
        if (cached != null) return new JSONObject(cached.toString());
        Configuration config=Resources.getSystem().getConfiguration();
        int type=config.uiMode & Configuration.UI_MODE_TYPE_MASK;
        String category=type==Configuration.UI_MODE_TYPE_TELEVISION ? "tv"
                : type==Configuration.UI_MODE_TYPE_WATCH ? "watch"
                : type==Configuration.UI_MODE_TYPE_CAR ? "car"
                : config.smallestScreenWidthDp>=600 ? "tablet" : "phone";
        String product="";
        // Optional vendor property: not all manufacturers expose a retail name.
        Process process=null;
        try {
            process=new ProcessBuilder("/system/bin/getprop","ro.product.marketname").start();
            if (process.waitFor(800,java.util.concurrent.TimeUnit.MILLISECONDS) && process.exitValue()==0) {
                byte[] bytes=new byte[256]; int length=process.getInputStream().read(bytes);
                if (length>0) product=new String(bytes,0,length,java.nio.charset.StandardCharsets.UTF_8).trim();
            }
        } catch (Exception ignored) { /* Model remains available on every Android device. */ }
        finally { if (process != null) process.destroy(); }
        cached=new JSONObject().put("brand",Build.BRAND).put("manufacturer",Build.MANUFACTURER)
                .put("model",Build.MODEL).put("productName",product)
                .put("category",category).put("androidVersion",Build.VERSION.RELEASE);
        return new JSONObject(cached.toString());
    }
}
