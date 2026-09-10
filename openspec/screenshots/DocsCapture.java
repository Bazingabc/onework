package com.one.onework;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.os.Bundle;
import android.view.View;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Method;

/** Renders only the isolated demo app's content, never the device screen. */
public final class DocsCapture extends Instrumentation {
    @Override public void onCreate(Bundle args) { super.onCreate(args); start(); }
    @Override public void onStart() {
        Bundle result = new Bundle();
        Activity activity = null;
        try {
            Intent intent = new Intent().setClassName(getTargetContext().getPackageName(), MainActivity.class.getName());
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity = startActivitySync(intent);
            Thread.sleep(2500);
            capture(activity, "android-home.png");
            Activity target = activity;
            runOnMainSync(() -> {
                try {
                    Method show = MainActivity.class.getDeclaredMethod("showPage", int.class);
                    show.setAccessible(true); show.invoke(target, 1);
                } catch (Exception e) { throw new IllegalStateException(e); }
            });
            Thread.sleep(2000);
            capture(activity, "android-agents.png");
            result.putString("stream", "Mock UI screenshots captured; no network permission.\n");
            finish(Activity.RESULT_OK, result);
        } catch (Exception e) {
            result.putString("stream", e.toString()); finish(Activity.RESULT_CANCELED, result);
        } finally {
            if (activity != null) { Activity target = activity; runOnMainSync(target::finish); }
        }
    }

    private void capture(Activity activity, String filename) {
        runOnMainSync(() -> {
            View view = activity.findViewById(android.R.id.content);
            int width = 1600, footer = 38;
            float scale = (float) width / view.getWidth();
            int height = Math.round(view.getHeight() * scale);
            Bitmap bitmap = Bitmap.createBitmap(width, height + footer, Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            canvas.drawColor(Color.WHITE);
            canvas.save(); canvas.scale(scale, scale); view.draw(canvas); canvas.restore();
            Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
            paint.setColor(Color.rgb(104, 113, 132)); paint.setTextSize(18);
            canvas.drawText("DEMO · 模拟任务、待办与额度 · 非真实账户数据", 28, height + 25, paint);
            try (FileOutputStream out = new FileOutputStream(new File(activity.getFilesDir(), filename))) {
                if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)) throw new IllegalStateException("PNG capture failed");
            } catch (Exception e) { throw new IllegalStateException(e); }
            bitmap.recycle();
        });
    }
}
