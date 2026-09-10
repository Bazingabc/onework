package com.one.onework;

import org.json.JSONObject;

final class SessionItem {
    final String id;
    final String title;
    final String preview;
    final String cwd;
    final String source;
    final String ownership;
    final JSONObject attention;
    final JSONObject control;
    final String runtimeState;
    final String attentionType;
    final String responseMode;
    final String attentionQuestion;
    final String attentionMessageId;
    final String controlType;
    final String controlReason;
    final String statusType;
    final String statusLabel;
    final String statusMessage;
    final long createdAt;
    final long updatedAt;
    final JSONObject pending;

    SessionItem(JSONObject value) {
        id = value.optString("id");
        title = value.optString("title", "未命名会话");
        preview = value.optString("preview");
        cwd = value.optString("cwd");
        source = value.optString("source", "unknown");
        ownership = value.optString("ownership", "managed");
        runtimeState = value.optString("runtimeState", "unknown");
        attention = value.optJSONObject("attention");
        attentionType = attention == null ? "none" : attention.optString("type", "none");
        responseMode = attention == null ? "" : attention.optString("responseMode");
        attentionQuestion = attention == null ? "" : attention.optString("question");
        attentionMessageId = attention == null ? "" : attention.optString("messageId");
        control = value.optJSONObject("control");
        controlType = control == null
                ? ("managed".equals(ownership) ? "direct" : "busy_elsewhere")
                : control.optString("type", "busy_elsewhere");
        controlReason = control == null ? "" : control.optString("reason");
        JSONObject status = value.optJSONObject("status");
        statusType = status == null ? "error" : status.optString("type", "error");
        String providedLabel = status == null ? "" : status.optString("label");
        statusLabel = providedLabel.isEmpty()
                ? SessionState.fallbackLabel(statusType)
                : providedLabel;
        statusMessage = status == null ? "" : status.optString("message");
        createdAt = value.optLong("createdAt");
        updatedAt = value.optLong("updatedAt");
        pending = value.optJSONObject("pending");
    }

    SessionItem(
            String id,
            String title,
            String ownership,
            String statusType,
            long updatedAt) {
        this(
                id,
                title,
                statusType,
                "waiting_input".equals(statusType) || "waiting_approval".equals(statusType)
                        ? "required_protocol"
                        : "none",
                "managed".equals(ownership) ? "direct" : "busy_elsewhere",
                "running".equals(statusType) ? "running" : "settled",
                updatedAt);
    }

    SessionItem(
            String id,
            String title,
            String statusType,
            String attentionType,
            String controlType,
            String runtimeState,
            long updatedAt) {
        this(
                id,
                title,
                statusType,
                attentionType,
                controlType,
                runtimeState,
                updatedAt,
                "");
    }

    SessionItem(
            String id,
            String title,
            String statusType,
            String attentionType,
            String controlType,
            String runtimeState,
            long updatedAt,
            String attentionMessageId) {
        this.id = id;
        this.title = title;
        this.preview = "";
        this.cwd = "";
        this.source = "appServer";
        this.ownership = "managed";
        this.attention = null;
        this.control = null;
        this.runtimeState = runtimeState;
        this.attentionType = attentionType;
        this.responseMode = "";
        this.attentionQuestion = "";
        this.attentionMessageId = attentionMessageId;
        this.controlType = controlType;
        this.controlReason = "";
        this.statusType = statusType;
        this.statusLabel = SessionState.fallbackLabel(statusType);
        this.statusMessage = "";
        this.createdAt = updatedAt;
        this.updatedAt = updatedAt;
        this.pending = null;
    }

    boolean isManaged() {
        return true;
    }

    boolean isWritable(boolean online) {
        return SessionState.isWritable(controlType, statusType, online);
    }

    boolean needsAttention() {
        return "required_protocol".equals(attentionType)
                || "required_inferred".equals(attentionType)
                || "waiting_input".equals(statusType)
                || "waiting_approval".equals(statusType);
    }

    boolean possiblyNeedsAttention() {
        return "possible_inferred".equals(attentionType)
                || "possible_input".equals(statusType);
    }

    boolean hasInferredAttention() {
        return "required_inferred".equals(attentionType)
                || "possible_inferred".equals(attentionType);
    }

    boolean hasAttentionCard() {
        return !"none".equals(attentionType);
    }

    boolean isAttachable() {
        return "attachable".equals(controlType);
    }

    boolean isBusyElsewhere() {
        return "busy_elsewhere".equals(controlType);
    }

    boolean isError() {
        return "error".equals(statusType) || "offline".equals(statusType);
    }

    boolean isRunning() {
        return "running".equals(runtimeState) || "running".equals(statusType);
    }

    boolean isIdle() {
        return "settled".equals(runtimeState) || "idle".equals(statusType);
    }

    String workspaceLabel() {
        if (cwd.isEmpty()) return sourceLabel();
        String normalized = cwd.endsWith("/")
                ? cwd.substring(0, cwd.length() - 1)
                : cwd;
        int slash = normalized.lastIndexOf('/');
        String shortName = slash >= 0 ? normalized.substring(slash + 1) : normalized;
        return shortName.isEmpty() ? sourceLabel() : shortName;
    }

    String sourceLabel() {
        switch (source) {
            case "vscode": return "Codex 客户端";
            case "cli": return "CLI";
            case "exec": return "Exec";
            case "appServer": return "Agent Views";
            default: return "Codex";
        }
    }
}
