package com.oneripple.agentviews;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.text.format.DateUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

final class SessionRailAdapter extends BaseAdapter {
    enum Section {
        ERROR,
        ATTENTION,
        POSSIBLE,
        RUNNING,
        COMPLETED
    }

    static final class Row {
        final Section section;
        final SessionItem session;
        final int count;
        final boolean collapsed;

        private Row(Section section, SessionItem session, int count, boolean collapsed) {
            this.section = section;
            this.session = session;
            this.count = count;
            this.collapsed = collapsed;
        }

        static Row section(Section section, int count, boolean collapsed) {
            return new Row(section, null, count, collapsed);
        }

        static Row session(Section section, SessionItem session) {
            return new Row(section, session, 0, false);
        }

        boolean isSection() {
            return session == null;
        }

        boolean isCollapsible() {
            return section == Section.COMPLETED || section == Section.POSSIBLE;
        }
    }

    private static final int TYPE_SECTION = 0;
    private static final int TYPE_SESSION = 1;

    private final Context context;
    private final List<Row> rows = new ArrayList<>();
    private Map<String, Long> waitingSince = Collections.emptyMap();
    private String selectedId;
    private boolean online;
    private long nowMs;

    SessionRailAdapter(Context context) {
        this.context = context;
    }

    void replace(
            DashboardState state,
            String selectedId,
            boolean online,
            boolean completedCollapsed,
            boolean possibleCollapsed,
            Map<String, Long> waitingSince,
            long nowMs) {
        rows.clear();
        if (!state.errors.isEmpty()) {
            append(Section.ERROR, state.errors, false);
        }
        append(Section.ATTENTION, state.attention, false);
        if (!state.possible.isEmpty()) {
            append(Section.POSSIBLE, state.possible, possibleCollapsed);
        }
        append(Section.RUNNING, state.running, false);
        if (!state.completed.isEmpty()) {
            append(Section.COMPLETED, state.completed, completedCollapsed);
        }
        this.selectedId = selectedId;
        this.online = online;
        this.waitingSince = new HashMap<>(waitingSince);
        this.nowMs = nowMs;
        notifyDataSetChanged();
    }

    private void append(Section section, List<SessionItem> values, boolean collapsed) {
        rows.add(Row.section(section, values.size(), collapsed));
        if (!collapsed) {
            for (SessionItem item : values) rows.add(Row.session(section, item));
        }
    }

    void setSelectedId(String selectedId) {
        this.selectedId = selectedId;
        notifyDataSetChanged();
    }

    void setOnline(boolean online) {
        this.online = online;
        notifyDataSetChanged();
    }

    Row rowAt(int position) {
        return rows.get(position);
    }

    @Override
    public int getCount() {
        return rows.size();
    }

    @Override
    public Row getItem(int position) {
        return rows.get(position);
    }

    @Override
    public long getItemId(int position) {
        Row row = rows.get(position);
        return row.session == null
                ? -(row.section.ordinal() + 1L)
                : row.session.id.hashCode();
    }

    @Override
    public int getViewTypeCount() {
        return 2;
    }

    @Override
    public int getItemViewType(int position) {
        return rows.get(position).isSection() ? TYPE_SECTION : TYPE_SESSION;
    }

    @Override
    public boolean areAllItemsEnabled() {
        return false;
    }

    @Override
    public boolean isEnabled(int position) {
        Row row = rows.get(position);
        return !row.isSection() || row.isCollapsible();
    }

    @Override
    public View getView(int position, View reusable, ViewGroup parent) {
        Row row = rows.get(position);
        if (row.isSection()) return sectionView(row, reusable);
        return sessionView(row, reusable);
    }

    private View sectionView(Row row, View reusable) {
        TextView view;
        if (reusable instanceof TextView) {
            view = (TextView) reusable;
        } else {
            view = new TextView(context);
            view.setTextSize(12);
            view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            view.setGravity(Gravity.CENTER_VERTICAL);
            view.setPadding(dp(12), dp(8), dp(10), 0);
            view.setMinHeight(dp(38));
            view.setLetterSpacing(0.03f);
        }
        String disclosure = row.isCollapsible() ? (row.collapsed ? "  ▸" : "  ▾") : "";
        view.setText(context.getString(
                R.string.section_count,
                sectionLabel(row.section),
                row.count,
                disclosure));
        view.setTextColor(sectionColor(row.section));
        view.setContentDescription(
                sectionLabel(row.section)
                        + "，"
                        + row.count
                        + " 项"
                        + (row.isCollapsible() ? (row.collapsed ? "，已折叠" : "，已展开") : ""));
        view.setBackgroundColor(AgentTheme.SURFACE);
        return view;
    }

    private View sessionView(Row row, View reusable) {
        SessionHolder holder;
        if (reusable == null || !(reusable.getTag() instanceof SessionHolder)) {
            LinearLayout root = new LinearLayout(context);
            root.setOrientation(LinearLayout.HORIZONTAL);
            root.setGravity(Gravity.FILL_VERTICAL);
            root.setPadding(dp(8), dp(4), dp(8), dp(4));
            root.setMinimumHeight(dp(78));

            View accent = new View(context);
            root.addView(accent, new LinearLayout.LayoutParams(dp(3), ViewGroup.LayoutParams.MATCH_PARENT));

            LinearLayout content = new LinearLayout(context);
            content.setOrientation(LinearLayout.VERTICAL);
            content.setGravity(Gravity.CENTER_VERTICAL);
            content.setPadding(dp(11), dp(8), dp(8), dp(8));

            LinearLayout firstLine = new LinearLayout(context);
            firstLine.setOrientation(LinearLayout.HORIZONTAL);
            firstLine.setGravity(Gravity.CENTER_VERTICAL);
            TextView title = new TextView(context);
            title.setTextColor(AgentTheme.TEXT);
            title.setTextSize(14);
            title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            title.setSingleLine(true);
            title.setEllipsize(TextUtils.TruncateAt.END);
            firstLine.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

            TextView status = new TextView(context);
            status.setTextSize(11);
            status.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            status.setGravity(Gravity.END);
            firstLine.addView(status, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT));
            content.addView(firstLine);

            TextView meta = new TextView(context);
            meta.setTextColor(AgentTheme.MUTED);
            meta.setTextSize(11);
            meta.setSingleLine(true);
            meta.setEllipsize(TextUtils.TruncateAt.END);
            LinearLayout.LayoutParams metaParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            metaParams.topMargin = dp(5);
            content.addView(meta, metaParams);
            root.addView(content, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));

            holder = new SessionHolder(root, accent, title, status, meta);
            root.setTag(holder);
            reusable = root;
        } else {
            holder = (SessionHolder) reusable.getTag();
        }

        SessionItem item = row.session;
        boolean selected = item.id.equals(selectedId);
        String displayStatus = online ? item.statusLabel : "离线快照";
        int color = statusColor(online ? item.statusType : "offline");
        holder.root.setBackground(cardBackground(selected, color));
        holder.root.setAlpha(1f);
        holder.accent.setBackgroundColor(color);
        holder.title.setText(item.title);
        holder.status.setText(displayStatus);
        holder.status.setTextColor(color);

        CharSequence relative = item.updatedAt > 0
                ? DateUtils.getRelativeTimeSpanString(
                        item.updatedAt * 1_000L,
                        nowMs,
                        DateUtils.MINUTE_IN_MILLIS,
                        DateUtils.FORMAT_ABBREV_RELATIVE)
                : "时间未知";
        String timing = item.needsAttention() && online
                ? waitingDuration(item.id)
                : relative.toString();
        String meta = item.workspaceLabel() + "  ·  " + timing;
        holder.meta.setText(meta);
        holder.root.setContentDescription(
                item.title
                        + "，"
                        + displayStatus
                        + "，"
                        + item.workspaceLabel()
                        + "，"
                        + timing
                        + (item.isWritable(online) ? "，可操作" : "，暂不可操作"));
        return reusable;
    }

    private String waitingDuration(String id) {
        long since = waitingSince.getOrDefault(id, nowMs);
        long minutes = Math.max(0L, (nowMs - since) / DateUtils.MINUTE_IN_MILLIS);
        if (minutes < 1) return "刚刚开始等待";
        if (minutes < 60) return "已等待 " + minutes + " 分钟";
        long hours = minutes / 60;
        long remaining = minutes % 60;
        return remaining == 0
                ? "已等待 " + hours + " 小时"
                : "已等待 " + hours + " 小时 " + remaining + " 分";
    }

    private String sectionLabel(Section section) {
        switch (section) {
            case ERROR: return "异常";
            case ATTENTION: return "需要你处理";
            case POSSIBLE: return "可能需要你";
            case RUNNING: return "运行中";
            case COMPLETED: return "最近完成";
            default: return "会话";
        }
    }

    private int sectionColor(Section section) {
        switch (section) {
            case ERROR: return AgentTheme.ERROR;
            case ATTENTION: return AgentTheme.ATTENTION;
            case POSSIBLE: return AgentTheme.ATTENTION;
            case RUNNING: return AgentTheme.RUNNING;
            case COMPLETED: return AgentTheme.SUCCESS;
            default: return AgentTheme.MUTED;
        }
    }

    private int statusColor(String type) {
        switch (type) {
            case "running": return AgentTheme.RUNNING;
            case "waiting_input":
            case "waiting_approval": return AgentTheme.ATTENTION;
            case "possible_input": return AgentTheme.ATTENTION;
            case "idle": return AgentTheme.SUCCESS;
            case "error":
            case "offline": return AgentTheme.ERROR;
            default: return AgentTheme.MUTED;
        }
    }

    private GradientDrawable cardBackground(boolean selected, int accent) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(selected ? (accent == AgentTheme.ATTENTION
                ? AgentTheme.ATTENTION_SURFACE : AgentTheme.RAISED) : AgentTheme.SURFACE);
        background.setCornerRadius(dp(8));
        if (selected) background.setStroke(dp(1), accent);
        return background;
    }

    private int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    private static final class SessionHolder {
        final LinearLayout root;
        final View accent;
        final TextView title;
        final TextView status;
        final TextView meta;

        SessionHolder(
                LinearLayout root,
                View accent,
                TextView title,
                TextView status,
                TextView meta) {
            this.root = root;
            this.accent = accent;
            this.title = title;
            this.status = status;
            this.meta = meta;
        }
    }
}
