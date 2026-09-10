package com.one.onework;

import java.util.List;
import java.util.Map;

final class AttentionEvent {
    enum Kind {
        NONE,
        NEW_ATTENTION,
        NEW_POSSIBLE,
        NEW_RUNNING,
        ERROR,
        COMPLETED,
        RECOVERED,
        CONNECTION_LOST,
        CONNECTION_RESTORED,
        SUBMITTED
    }

    enum Tone {
        NEUTRAL,
        ATTENTION,
        POSSIBLE,
        RUNNING,
        SUCCESS,
        ERROR
    }

    static final AttentionEvent NONE = new AttentionEvent(
            Kind.NONE,
            Tone.NEUTRAL,
            null,
            "",
            false);

    final Kind kind;
    final Tone tone;
    final String sessionId;
    final String message;
    final boolean persistent;

    private AttentionEvent(
            Kind kind,
            Tone tone,
            String sessionId,
            String message,
            boolean persistent) {
        this.kind = kind;
        this.tone = tone;
        this.sessionId = sessionId;
        this.message = message;
        this.persistent = persistent;
    }

    static AttentionEvent between(
            List<SessionItem> before,
            List<SessionItem> after,
            boolean hasBaseline) {
        if (!hasBaseline) return NONE;
        Map<String, SessionItem> previous = DashboardState.index(before);
        List<FocusEvent> focusEvents = FocusEvent.between(before, after, true);
        if (!focusEvents.isEmpty()) return detected(focusEvents.get(0));
        for (SessionItem current : after) {
            SessionItem old = previous.get(current.id);
            if (old != null && old.isRunning() && current.isIdle()) {
                return sessionEvent(
                        Kind.COMPLETED,
                        Tone.SUCCESS,
                        current,
                        "刚刚完成任务",
                        false);
            }
        }
        for (SessionItem current : after) {
            SessionItem old = previous.get(current.id);
            if (old != null && old.isError() && !current.isError()) {
                return sessionEvent(
                        Kind.RECOVERED,
                        Tone.SUCCESS,
                        current,
                        "已恢复",
                        false);
            }
        }
        return NONE;
    }

    static AttentionEvent detected(FocusEvent event) {
        switch (event.kind) {
            case ERROR:
                return focusEvent(
                        Kind.ERROR,
                        Tone.ERROR,
                        event,
                        "出现异常，需要你检查恢复",
                        true,
                        "检测到变化 · ");
            case REQUIRED:
                return focusEvent(
                        Kind.NEW_ATTENTION,
                        Tone.ATTENTION,
                        event,
                        "正在等待你的回答",
                        false,
                        "检测到变化 · ");
            case POSSIBLE:
                return focusEvent(
                        Kind.NEW_POSSIBLE,
                        Tone.POSSIBLE,
                        event,
                        "可能需要你的回复",
                        false,
                        "检测到变化 · ");
            default:
                return focusEvent(
                        Kind.NEW_RUNNING,
                        Tone.RUNNING,
                        event,
                        "开始运行",
                        false,
                        "检测到变化 · ");
        }
    }

    static AttentionEvent autoFocused(FocusEvent event) {
        AttentionEvent detected = detected(event);
        return new AttentionEvent(
                detected.kind,
                detected.tone,
                detected.sessionId,
                detected.message.replace("检测到变化 · ", "已自动切换 · "),
                detected.persistent);
    }

    static AttentionEvent connectionLost(String detail) {
        String suffix = detail == null || detail.trim().isEmpty() ? "" : " · " + detail;
        return new AttentionEvent(
                Kind.CONNECTION_LOST,
                Tone.ERROR,
                null,
                "Mac 连接中断" + suffix,
                true);
    }

    static AttentionEvent connectionRestored() {
        return new AttentionEvent(
                Kind.CONNECTION_RESTORED,
                Tone.SUCCESS,
                null,
                "Mac 已重新连接 · 状态已同步",
                false);
    }

    static AttentionEvent submitted(SessionItem item) {
        return new AttentionEvent(
                Kind.SUBMITTED,
                Tone.SUCCESS,
                item == null ? null : item.id,
                "已提交 · Agent 已继续运行",
                false);
    }

    static AttentionEvent created(SessionItem item) {
        return new AttentionEvent(
                Kind.RECOVERED,
                Tone.SUCCESS,
                item == null ? null : item.id,
                "已新建 Codex 会话 · 可以从平板开始任务",
                false);
    }

    static AttentionEvent dismissed(SessionItem item) {
        return new AttentionEvent(
                Kind.RECOVERED,
                Tone.NEUTRAL,
                item == null ? null : item.id,
                "已忽略这条文本卡点",
                false);
    }

    static AttentionEvent sessionUnavailable(String title) {
        String safeTitle = title == null || title.trim().isEmpty() ? "当前会话" : title;
        return new AttentionEvent(
                Kind.ERROR,
                Tone.ERROR,
                null,
                safeTitle + "已不在最近一小时范围内 · 草稿仍保留",
                false);
    }

    boolean isVisible() {
        return kind != Kind.NONE;
    }

    boolean shouldFocus() {
        return kind == Kind.NEW_ATTENTION || kind == Kind.ERROR;
    }

    private static AttentionEvent sessionEvent(
            Kind kind,
            Tone tone,
            SessionItem item,
            String action,
            boolean persistent) {
        return new AttentionEvent(
                kind,
                tone,
                item.id,
                "刚刚变化 · " + item.title + action,
                persistent);
    }

    private static AttentionEvent focusEvent(
            Kind kind,
            Tone tone,
            FocusEvent event,
            String action,
            boolean persistent,
            String prefix) {
        return new AttentionEvent(
                kind,
                tone,
                event.sessionId,
                prefix + event.title + " · " + action,
                persistent);
    }
}
