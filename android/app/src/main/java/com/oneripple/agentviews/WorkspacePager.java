package com.oneripple.agentviews;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Rect;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import java.util.function.BooleanSupplier;
import java.util.function.IntConsumer;

/** Two retained pages. Only content translates; branding and pagination stay anchored. */
final class WorkspacePager extends FrameLayout {
    private float downX, downY, origin, position;
    private int page;
    private boolean tracking, dragging, rejected, touching;
    private VelocityTracker velocity;
    private ValueAnimator animator;
    private View protectedView;
    private final int slop;
    private final float fling;
    BooleanSupplier canNavigate = () -> true;
    IntConsumer onPage = index -> {};

    WorkspacePager(Context context) {
        super(context);
        slop = ViewConfiguration.get(context).getScaledTouchSlop();
        fling = 550 * getResources().getDisplayMetrics().density;
        setClipChildren(true);
    }

    void protect(View view) { protectedView = view; }
    boolean isInteracting() { return touching || dragging || animator != null; }

    void show(int target) {
        if (target == page || !canNavigate.getAsBoolean() || touching) return;
        settle(target, true);
    }

    @Override public boolean dispatchTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) touching = true;
        boolean handled = super.dispatchTouchEvent(event);
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) touching = false;
        return handled;
    }

    private void begin(MotionEvent event) {
        cancelAnimation();
        recycleVelocity();
        velocity = VelocityTracker.obtain();
        velocity.addMovement(event);
        downX = event.getX(); downY = event.getY(); origin = position;
        tracking = true; dragging = false;
        Rect rect = new Rect();
        rejected = !canNavigate.getAsBoolean() || (protectedView != null
                && protectedView.getGlobalVisibleRect(rect)
                && rect.contains((int) event.getRawX(), (int) event.getRawY()));
    }

    @Override public boolean onInterceptTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) { begin(event); return false; }
        if (!tracking) return false;
        if (velocity != null) velocity.addMovement(event);
        if (action == MotionEvent.ACTION_POINTER_DOWN) {
            rejected = true;
            return dragging;
        }
        if (action == MotionEvent.ACTION_MOVE && !rejected) {
            float dx = event.getX() - downX, dy = event.getY() - downY;
            if (Math.abs(dy) > slop && Math.abs(dy) > Math.abs(dx)) rejected = true;
            if (Math.abs(dx) > slop && Math.abs(dx) > Math.abs(dy) * 1.5f) {
                dragging = true;
                getParent().requestDisallowInterceptTouchEvent(true);
                exposePages();
                move(origin + dx);
                return true;
            }
        }
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            tracking = false; recycleVelocity();
            // A touch may have interrupted a programmatic animation without becoming a drag.
            if (position != -page * getWidth()) settle(page, true);
        }
        return dragging;
    }

    @Override public boolean onTouchEvent(MotionEvent event) {
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN && !tracking) begin(event);
        if (velocity != null) velocity.addMovement(event);
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_MOVE && tracking && !dragging && !rejected) {
            float dx = event.getX() - downX, dy = event.getY() - downY;
            if (Math.abs(dy) > slop && Math.abs(dy) > Math.abs(dx)) rejected = true;
            if (Math.abs(dx) > slop && Math.abs(dx) > Math.abs(dy) * 1.5f) {
                dragging = true; exposePages();
            }
        }
        if (action == MotionEvent.ACTION_MOVE && dragging && !rejected) move(origin + event.getX() - downX);
        if (action == MotionEvent.ACTION_POINTER_DOWN) rejected = true;
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            if (action == MotionEvent.ACTION_UP && !dragging && !rejected) performClick();
            float speed = 0;
            if (velocity != null) { velocity.computeCurrentVelocity(1000); speed = velocity.getXVelocity(); }
            int target = action == MotionEvent.ACTION_CANCEL || rejected || !canNavigate.getAsBoolean()
                    ? page : PageSnap.target(position, speed, getWidth(), fling);
            tracking = false; dragging = false; recycleVelocity();
            settle(target, true);
        }
        return true;
    }

    private void settle(int target, boolean animate) {
        cancelAnimation();
        page = target;
        onPage.accept(page);
        float finish = -page * getWidth();
        if (!animate || !ValueAnimator.areAnimatorsEnabled() || Math.abs(position - finish) < 1) {
            move(finish); rest(); return;
        }
        exposePages();
        ValueAnimator next = ValueAnimator.ofFloat(position, finish);
        animator = next;
        next.setDuration(250);
        next.setInterpolator(new DecelerateInterpolator(2f));
        next.addUpdateListener(value -> move((float) value.getAnimatedValue()));
        next.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(android.animation.Animator animation) {
                if (animator != next) return;
                animator = null; move(finish); rest();
            }
        });
        next.start();
    }

    @Override public boolean performClick() { super.performClick(); return true; }

    private void exposePages() {
        for (int i = 0; i < getChildCount(); i++) {
            getChildAt(i).setVisibility(VISIBLE);
            getChildAt(i).setImportantForAccessibility(i == page
                    ? IMPORTANT_FOR_ACCESSIBILITY_AUTO : IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        }
    }

    private void rest() {
        exposePages();
        for (int i = 0; i < getChildCount(); i++) getChildAt(i).setVisibility(i == page ? VISIBLE : INVISIBLE);
    }

    private void move(float value) {
        position = PageSnap.clamp(value, getWidth());
        for (int i = 0; i < getChildCount(); i++) getChildAt(i).setTranslationX(position + i * getWidth());
    }

    private void cancelAnimation() {
        ValueAnimator previous = animator; animator = null;
        if (previous != null) previous.cancel();
    }
    private void recycleVelocity() { if (velocity != null) { velocity.recycle(); velocity = null; } }

    @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);
        cancelAnimation(); dragging = false; move(-page * w); rest();
    }
    @Override protected void onDetachedFromWindow() {
        cancelAnimation(); recycleVelocity(); touching = false; tracking = false; dragging = false;
        super.onDetachedFromWindow();
    }
}
