package com.oneripple.agentviews;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/** One 24-unit, 1.8-unit-stroke icon family; no font glyph substitutions. */
final class WorkIcon extends Drawable {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final String kind;
    private final int size;
    WorkIcon(Context context, String kind, int color) {
        this.kind = kind; size = OneWorkUi.dp(context, 24);
        paint.setColor(color); paint.setStrokeWidth(1.8f);
        paint.setStrokeCap(Paint.Cap.ROUND); paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStyle(Paint.Style.STROKE);
    }
    @Override public void draw(Canvas c) {
        c.save(); c.translate(getBounds().left, getBounds().top);
        c.scale(getBounds().width() / 24f, getBounds().height() / 24f);
        switch (kind) {
            case "refresh":
                c.drawArc(new RectF(4, 4, 20, 20), 35, 290, false, paint);
                line(c, 20, 3, 20, 8, 15, 8); break;
            case "arrow": line(c, 9, 5, 16, 12, 9, 19); break;
            case "down": line(c, 6, 9, 12, 15, 18, 9); break;
            case "task":
                c.drawRoundRect(new RectF(4, 4, 20, 20), 3, 3, paint);
                line(c, 8, 12, 11, 15, 16, 9); break;
            case "checkbox": c.drawRoundRect(new RectF(4, 4, 20, 20), 3, 3, paint); break;
            case "agents":
                for (int x : new int[]{4, 14}) for (int y : new int[]{4, 14})
                    c.drawRoundRect(new RectF(x, y, x + 6, y + 6), 1, 1, paint);
                break;
            case "quota":
                c.drawLine(5, 19, 5, 12, paint); c.drawLine(12, 19, 12, 5, paint);
                c.drawLine(19, 19, 19, 9, paint); break;
            case "minutes":
                c.drawLine(3, 10, 3, 14, paint); c.drawLine(7, 6, 7, 18, paint);
                c.drawLine(12, 3, 12, 21, paint); c.drawLine(17, 7, 17, 17, paint);
                c.drawLine(21, 10, 21, 14, paint); break;
            case "settings":
                c.drawCircle(12, 12, 3, paint); c.drawCircle(12, 12, 7, paint);
                for (int i = 0; i < 8; i++) { c.save(); c.rotate(i * 45, 12, 12); c.drawLine(12, 3, 12, 5, paint); c.restore(); }
                break;
            case "voice":
                c.drawRoundRect(new RectF(9, 3, 15, 14), 3, 3, paint);
                c.drawArc(new RectF(6, 7, 18, 18), 0, 180, false, paint);
                c.drawLine(12, 18, 12, 21, paint); c.drawLine(9, 21, 15, 21, paint); break;
            default: c.drawCircle(12, 12, 7, paint);
        }
        c.restore();
    }
    private void line(Canvas c, float... points) {
        Path path = new Path(); path.moveTo(points[0], points[1]);
        for (int i = 2; i < points.length; i += 2) path.lineTo(points[i], points[i + 1]);
        c.drawPath(path, paint);
    }
    @Override public int getIntrinsicWidth() { return size; }
    @Override public int getIntrinsicHeight() { return size; }
    @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); invalidateSelf(); }
    @Override public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); invalidateSelf(); }
    @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
