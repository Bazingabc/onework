package com.one.onework;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import org.json.*;
import java.util.concurrent.*;

final class HomePane extends LinearLayout {
    private final Activity activity;
    private final ApiClient api = new ApiClient();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final TextView sync, overview, runningCount, pendingCount, pendingLabel, taskCount, quotaHeading;
    private final LinearLayout tasks, quotas;
    private final java.util.function.Consumer<String> openSession;
    private String attentionId;
    private boolean bridgeOnline;
    private final Button refresh;
    private final Button quotaToggle;
    private JSONObject lastQuota;
    private boolean allQuotas;
    private int quotaWindows;
    private boolean active, loading;
    private String renderedTasks = "";
    private final java.util.Set<String> uncertain = new java.util.HashSet<>();
    volatile boolean busy;
    private final Runnable tick = () -> load(false);

    HomePane(Activity activity, Runnable openAgents, java.util.function.Consumer<String> openSession) {
        super(activity);
        this.activity = activity;
        this.openSession = openSession;
        setOrientation(VERTICAL);
        setPadding(d(24), d(16), d(24), d(4));
        LinearLayout columns = new LinearLayout(activity);
        addView(columns, new LayoutParams(-1, 0, 1));
        LinearLayout left = panel();
        LayoutParams leftParams = new LayoutParams(0, -1, 1.65f);
        leftParams.rightMargin = d(20);
        columns.addView(left, leftParams);
        LinearLayout heading = new LinearLayout(activity);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        heading.addView(sectionTitle("飞书待办", "task"), new LayoutParams(0, -2, 1));
        taskCount = label("", 12, AgentTheme.MUTED);
        heading.addView(taskCount);
        refresh = OneWorkUi.iconButton(activity, "refresh", "手动刷新飞书待办");
        refresh.setOnClickListener(v -> load(true));
        LayoutParams refreshParams = new LayoutParams(d(48), d(48));
        refreshParams.leftMargin = d(8);
        heading.addView(refresh, refreshParams);
        left.addView(heading);
        sync = label("正在同步…", 11, AgentTheme.MUTED);
        sync.setPadding(d(36), 0, 0, d(12));
        left.addView(sync);
        ScrollView scroll = new ScrollView(activity);
        tasks = new LinearLayout(activity);
        tasks.setOrientation(VERTICAL);
        scroll.addView(tasks);
        left.addView(scroll, new LayoutParams(-1, 0, 1));
        LinearLayout right = new LinearLayout(activity);
        right.setOrientation(VERTICAL);
        columns.addView(right, new LayoutParams(0, -1, 1));
        LinearLayout agent = panel();
        right.addView(agent, new LayoutParams(-1, -2));
        LinearLayout agentHeading = new LinearLayout(activity);
        agentHeading.setGravity(Gravity.CENTER_VERTICAL);
        agentHeading.addView(sectionTitle("Agent 总览", "agents"), new LayoutParams(0, -2, 1));
        Button open = OneWorkUi.iconButton(activity, "arrow", "查看全部 Agent 会话");
        open.setContentDescription("查看最近一小时 Agent 会话");
        open.setOnClickListener(v -> openAgents.run());
        agentHeading.addView(open, new LayoutParams(d(48), d(48)));
        agent.addView(agentHeading);
        TextView windowHint = label("最近一小时", 11, AgentTheme.MUTED);
        windowHint.setPadding(d(36), 0, 0, 0); agent.addView(windowHint);
        LinearLayout counts = new LinearLayout(activity);
        counts.setPadding(0, d(8), 0, d(8));
        LinearLayout running = new LinearLayout(activity); running.setOrientation(VERTICAL);
        runningCount = label("—", 26, AgentTheme.TEXT); running.addView(runningCount);
        running.addView(label("运行中", 12, AgentTheme.MUTED));
        counts.addView(running, new LayoutParams(0, -2, 1));
        LinearLayout pending = new LinearLayout(activity); pending.setOrientation(VERTICAL);
        pendingCount = label("—", 26, AgentTheme.TEXT); pending.addView(pendingCount);
        pendingLabel = label("待处理", 12, AgentTheme.MUTED); pending.addView(pendingLabel);
        counts.addView(pending, new LayoutParams(0, -2, 1));
        agent.addView(counts);
        overview = label("正在连接 Mac…", 13, AgentTheme.MUTED);
        overview.setPadding(d(12), d(8), d(12), d(8));
        overview.setGravity(Gravity.CENTER_VERTICAL);
        overview.setMaxLines(3);
        overview.setEllipsize(android.text.TextUtils.TruncateAt.END);
        overview.setMinHeight(d(64));
        overview.setBackground(OneWorkUi.surface(activity, AgentTheme.RAISED, 0));
        overview.setOnClickListener(v -> { if (bridgeOnline && attentionId != null) openSession.accept(attentionId); });
        agent.addView(overview, new LayoutParams(-1, d(72)));
        LinearLayout quotaPanel = panel();
        LayoutParams quotaParams = new LayoutParams(-1, 0, 1);
        quotaParams.topMargin = d(16);
        right.addView(quotaPanel, quotaParams);
        LinearLayout quotaBar = new LinearLayout(activity); quotaBar.setGravity(Gravity.CENTER_VERTICAL);
        quotaHeading = sectionTitle("Codex 额度", "quota");
        quotaBar.addView(quotaHeading, new LayoutParams(0, -2, 1));
        quotaToggle = OneWorkUi.iconButton(activity, "down", "展开全部额度窗口");
        quotaToggle.setOnClickListener(v -> { allQuotas = !allQuotas; renderQuota(lastQuota); });
        quotaBar.addView(quotaToggle, new LayoutParams(d(48), d(48)));
        quotaPanel.addView(quotaBar);
        quotas = new LinearLayout(activity); quotas.setOrientation(VERTICAL);
        ScrollView quotaScroll = new ScrollView(activity); quotaScroll.addView(quotas);
        quotaPanel.addView(quotaScroll, new LayoutParams(-1, 0, 1));
        LinearLayout minutes = panel(); minutes.setOrientation(HORIZONTAL); minutes.setGravity(Gravity.CENTER_VERTICAL);
        minutes.addView(icon("minutes"), new LayoutParams(d(24), d(24)));
        LinearLayout minutesCopy = new LinearLayout(activity); minutesCopy.setOrientation(VERTICAL);
        minutesCopy.setPadding(d(12), 0, 0, 0);
        minutesCopy.addView(label("飞书妙记", 16, AgentTheme.TEXT));
        minutesCopy.addView(label("打开妙记", 11, AgentTheme.MUTED));
        minutes.addView(minutesCopy, new LayoutParams(0, -2, 1));
        minutes.addView(icon("arrow"), new LayoutParams(d(20), d(20)));
        minutes.setFocusable(true);
        minutes.setContentDescription("打开飞书妙记，不自动录音");
        minutes.setOnClickListener(v -> {
            try {
                activity.startActivity(new Intent(Intent.ACTION_VIEW,
                        Uri.parse("feishu://applink.feishu.cn/minutes/home"))
                        .setPackage("com.ss.android.lark"));
            } catch (Exception e) {
                Toast.makeText(activity, "请先安装并登录飞书", Toast.LENGTH_LONG).show();
            }
        });
        LayoutParams minutesParams = new LayoutParams(-1, d(76));
        minutesParams.topMargin = d(16);
        right.addView(minutes, minutesParams);
    }

    void start() { active = true; load(false); }
    void stop() { active = false; main.removeCallbacks(tick); }
    void close() { stop(); worker.shutdownNow(); main.removeCallbacksAndMessages(null); }
    void overview(DashboardState state, boolean online) {
        overview(state, online, null);
    }
    void overview(DashboardState state, boolean online, String snapshotNotice) {
        bridgeOnline = online;
        int count = state.errors.size() + state.attentionCount() + state.possible.size();
        runningCount.setText(String.valueOf(state.runningCount()));
        pendingCount.setText(String.valueOf(count));
        boolean possibleOnly = state.errors.isEmpty() && state.attention.isEmpty() && !state.possible.isEmpty();
        pendingLabel.setText(possibleOnly ? "可能需要你" : "待处理");
        pendingCount.setTextColor(online && count > 0 ? AgentTheme.ATTENTION : AgentTheme.TEXT);
        SessionItem item = state.find(state.nextAttention(null));
        attentionId = item == null ? null : item.id;
        String value = !online ? "Mac 未连接 · 数据可能已过期" : snapshotNotice != null
                ? "Mac 已连接 · " + snapshotNotice : item == null
                ? "暂无需要你处理的事项" : item.title + "\n"
                + (item.attentionQuestion.isEmpty() ? item.statusLabel : item.attentionQuestion) + "  →";
        overview.setText(value);
        overview.setEnabled(online && snapshotNotice == null && item != null);
        overview.setTextColor(online && item != null ? AgentTheme.ATTENTION : AgentTheme.MUTED);
        overview.setBackground(OneWorkUi.surface(activity,
                online && item != null ? AgentTheme.ATTENTION_SURFACE : AgentTheme.RAISED, 0));
        overview.setContentDescription(value + (online && item != null ? "，打开对应会话" : ""));
    }

    private void load(boolean force) {
        if (loading || busy || !active) return;
        loading = true;
        main.removeCallbacks(tick);
        refresh.setEnabled(false);
        worker.execute(() -> {
            JSONObject result = null, usage = null;
            String error = null;
            try { result = api.workTasks(force); } catch (Exception e) { error = "同步失败，保留上次数据；可手动重试"; }
            try { usage = api.workUsage(); } catch (Exception ignored) {}
            JSONObject value = result, limits = usage;
            String failure = error;
            main.post(() -> {
                loading = false;
                refresh.setEnabled(true);
                if (value != null) renderTasks(value); else sync.setText(failure);
                renderQuota(limits);
                if (active) main.postDelayed(tick, 60_000);
            });
        });
    }

    private void renderTasks(JSONObject value) {
        long updated = value.optLong("updatedAt");
        String time = updated == 0 ? "尚未同步" : "同步于 " + android.text.format.DateFormat.format("HH:mm", updated * 1000);
        sync.setText(value.isNull("error") ? time + " · 每 5 分钟更新" : value.optString("error"));
        JSONArray rows = value.optJSONArray("items");
        taskCount.setText(rows == null ? "" : rows.length() + " 项");
        String signature = (rows == null ? "" : rows.toString()) + uncertain.toString();
        if (signature.equals(renderedTasks)) return;
        renderedTasks = signature;
        tasks.removeAllViews();
        if (rows == null || rows.length() == 0) {
            tasks.addView(label(updated == 0 ? "等待飞书数据" : "没有未完成待办", 18, AgentTheme.MUTED));
            return;
        }
        for (int i = 0; i < rows.length(); i++) {
            JSONObject item = rows.optJSONObject(i);
            if (item == null) continue;
            LinearLayout row = new LinearLayout(activity);
            row.setPadding(0, d(8), 0, d(8));
            row.setGravity(Gravity.CENTER_VERTICAL);
            LinearLayout copy = new LinearLayout(activity);
            copy.setOrientation(VERTICAL);
            TextView title = label(item.optString("summary"), 16, AgentTheme.TEXT);
            title.setMaxLines(2); title.setEllipsize(android.text.TextUtils.TruncateAt.END);
            title.setOnClickListener(v -> title.setMaxLines(title.getMaxLines() == 2 ? Integer.MAX_VALUE : 2));
            title.setContentDescription(item.optString("summary") + "，点击展开或收起标题");
            copy.addView(title);
            String due = item.optString("due_at", "");
            copy.addView(label(due.isEmpty() || due.equals("null") ? "未设截止时间" : "截止 " + due.replace('T', ' ').replace("+08:00", ""), 11, AgentTheme.MUTED));
            boolean needsVerify = uncertain.contains(item.optString("guid"));
            Button done = OneWorkUi.iconButton(activity, needsVerify ? "refresh" : "checkbox",
                    (needsVerify ? "核实待办：" : "完成待办：") + item.optString("summary"));
            done.setOnClickListener(v -> complete(item.optString("guid"), item.optString("revision"), done));
            row.addView(done, new LayoutParams(d(48), d(48)));
            LayoutParams copyParams = new LayoutParams(0, -2, 1); copyParams.leftMargin = d(8);
            row.addView(copy, copyParams);
            tasks.addView(row);
            View divider = new View(activity); divider.setBackgroundColor(AgentTheme.BORDER);
            tasks.addView(divider, new LayoutParams(-1, d(1)));
        }
    }

    private void complete(String id, String revision, Button button) {
        if (busy || loading) return;
        busy = true;
        button.setEnabled(false);
        button.setCompoundDrawables(null, null, null, null);
        button.setPadding(0, 0, 0, 0);
        button.setText("…");
        button.setContentDescription("正在向云端核实待办状态");
        worker.execute(() -> {
            String message;
            boolean verified = false;
            try {
                JSONObject result = uncertain.contains(id) ? api.verifyTask(id) : api.completeTask(id, revision);
                verified = true;
                message = result.optBoolean("confirmed") ? "已完成 · 云端已确认" : "云端仍未完成，可重新操作";
            } catch (Exception e) { message = e.getMessage(); }
            String feedback = message;
            boolean finalVerified = verified;
            main.post(() -> {
                busy = false;
                if (finalVerified) uncertain.remove(id); else uncertain.add(id);
                button.setEnabled(true);
                button.setText("");
                button.setPadding(d(12), 0, d(12), 0);
                button.setCompoundDrawablesRelativeWithIntrinsicBounds(new WorkIcon(activity,
                        finalVerified ? "checkbox" : "refresh", AgentTheme.MUTED), null, null, null);
                button.setContentDescription(finalVerified ? "完成待办" : "核实待办结果");
                Toast.makeText(activity, feedback, Toast.LENGTH_LONG).show();
                load(false);
            });
        });
    }

    private void renderQuota(JSONObject result) {
        lastQuota = result;
        quotaWindows = 0;
        quotaToggle.setVisibility(INVISIBLE);
        quotas.removeAllViews();
        if (result == null || !result.optBoolean("available")) { quotas.addView(label("额度暂不可用", 14, AgentTheme.MUTED)); return; }
        JSONObject data = result.optJSONObject("data");
        if (data == null) { quotas.addView(label("额度暂不可用", 14, AgentTheme.MUTED)); return; }
        JSONObject buckets = data.optJSONObject("rateLimitsByLimitId");
        if (buckets != null && buckets.length() > 0) {
            appendQuota("Codex", buckets.optJSONObject("codex"));
            java.util.Iterator<String> keys = buckets.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                if (key.equals("codex")) continue;
                JSONObject bucket = buckets.optJSONObject(key);
                appendQuota(bucket == null ? key : bucket.optString("limitName", key), bucket);
            }
        } else appendQuota("Codex", data.optJSONObject("rateLimits"));
        if (quotas.getChildCount() == 0) quotas.addView(label("额度暂不可用", 14, AgentTheme.MUTED));
        quotaToggle.setVisibility(quotaWindows > 1 ? VISIBLE : INVISIBLE);
        quotaToggle.setSelected(allQuotas);
        quotaToggle.setContentDescription((allQuotas ? "收起" : "展开") + "全部 " + quotaWindows + " 个额度窗口");
    }
    private void appendQuota(String name, JSONObject bucket) {
        if (bucket == null) return;
        for (String key : new String[]{"primary", "secondary"}) {
            JSONObject window = bucket.optJSONObject(key);
            if (window == null || !window.has("usedPercent") || window.isNull("usedPercent")) continue;
            quotaWindows++;
            if (!allQuotas && quotaWindows > 1) continue;
            int minutes = window.optInt("windowDurationMins");
            String duration = minutes <= 0 ? "当前窗口" : minutes % 1440 == 0 ? (minutes / 1440) + " 天"
                    : minutes % 60 == 0 ? (minutes / 60) + " 小时" : minutes + " 分钟";
            int remaining = Math.max(0, Math.min(100, 100 - window.optInt("usedPercent")));
            if (quotaWindows == 1) quotaHeading.setText(name + " 额度");
            TextView title = label((allQuotas ? name + " · " : "") + duration + "   " + remaining + "% 剩余", 13, AgentTheme.TEXT);
            title.setPadding(0, d(4), 0, d(8)); quotas.addView(title);
            ProgressBar meter = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
            meter.setMax(100); meter.setProgress(remaining);
            meter.setProgressTintList(android.content.res.ColorStateList.valueOf(AgentTheme.RUNNING));
            meter.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(AgentTheme.RAISED));
            meter.setContentDescription(name + "剩余 " + remaining + "%");
            quotas.addView(meter, new LayoutParams(-1, d(6)));
            long reset = window.optLong("resetsAt");
            if (reset > 0) {
                TextView resetLabel = label(android.text.format.DateFormat.format("MM-dd HH:mm", reset * 1000) + " 重置", 11, AgentTheme.MUTED);
                resetLabel.setPadding(0, d(6), 0, d(8)); quotas.addView(resetLabel);
            }
        }
    }
    private LinearLayout panel() {
        LinearLayout view = new LinearLayout(activity);
        view.setOrientation(VERTICAL);
        view.setPadding(d(18), d(12), d(18), d(12));
        view.setBackground(OneWorkUi.surface(activity, AgentTheme.SURFACE, AgentTheme.BORDER));
        return view;
    }
    private TextView label(String text, int size, int color) {
        TextView view = new TextView(activity);
        view.setText(text); view.setTextSize(size); view.setTextColor(color);
        if (size >= 16) view.setTypeface(android.graphics.Typeface.DEFAULT, android.graphics.Typeface.BOLD);
        return view;
    }
    private ImageView icon(String kind) {
        ImageView icon = new ImageView(activity);
        icon.setImageDrawable(new WorkIcon(activity, kind, AgentTheme.RUNNING));
        icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        return icon;
    }
    private TextView sectionTitle(String value, String kind) {
        TextView title = label(value, 18, AgentTheme.TEXT);
        title.setCompoundDrawablesRelativeWithIntrinsicBounds(new WorkIcon(activity, kind, AgentTheme.RUNNING), null, null, null);
        title.setCompoundDrawablePadding(d(12));
        title.setMaxLines(1); title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        return title;
    }
    private int d(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
