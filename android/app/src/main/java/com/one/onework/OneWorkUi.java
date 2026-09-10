package com.one.onework;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.widget.Button;

/** Shared native controls for the approved light OneWork workspace. */
final class OneWorkUi {
    static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    static GradientDrawable surface(Context context, int fill, int border) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(dp(context, 12));
        if (border != 0) shape.setStroke(dp(context, 1), border);
        return shape;
    }

    static Button button(Context context, String label, boolean primary) {
        Button button = new Button(context);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(14);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setMinHeight(dp(context, 48));
        button.setMinimumHeight(dp(context, 48));
        button.setPadding(dp(context, 14), 0, dp(context, 14), 0);
        button.setStateListAnimator(null);
        style(button, primary ? AgentTheme.RUNNING : AgentTheme.SURFACE,
                primary ? AgentTheme.ON_ACCENT : AgentTheme.TEXT, AgentTheme.BORDER);
        return button;
    }

    static Button iconButton(Context context, String icon, String description) {
        Button button = button(context, "", false);
        button.setPadding(dp(context, 12), 0, dp(context, 12), 0);
        button.setCompoundDrawablesRelativeWithIntrinsicBounds(new WorkIcon(context, icon, AgentTheme.MUTED), null, null, null);
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(0x223159CC),
                surface(context, AgentTheme.SURFACE, 0), null));
        button.setContentDescription(description);
        return button;
    }

    static void style(Button button, int fill, int foreground, int stroke) {
        int ink = fill == AgentTheme.RUNNING || fill == AgentTheme.ERROR
                || fill == AgentTheme.SUCCESS || fill == AgentTheme.ATTENTION
                ? AgentTheme.ON_ACCENT : foreground;
        button.setTextColor(new ColorStateList(new int[][] {
                {-android.R.attr.state_enabled}, {}
        }, new int[] {AgentTheme.MUTED, ink}));
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(0x223159CC),
                surface(button.getContext(), fill, stroke), null));
    }

    private OneWorkUi() {}
}
