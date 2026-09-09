package com.oneripple.agentviews;

final class SessionState {
    private SessionState() {}

    static String fallbackLabel(String type) {
        if (type == null) return "未知";
        switch (type) {
            case "running": return "运行中";
            case "waiting_input": return "等待输入";
            case "waiting_approval": return "等待审批";
            case "possible_input": return "可能需要你";
            case "idle": return "空闲";
            case "unknown": return "状态待确认";
            case "offline": return "未恢复";
            case "error": return "异常";
            default: return "未知";
        }
    }

    static boolean isWritable(String controlType, String type, boolean online) {
        if (!online
                || !("direct".equals(controlType) || "attachable".equals(controlType))) {
            return false;
        }
        return !"error".equals(type) && !"offline".equals(type);
    }

    static boolean shortcutMatches(String shortcut, String optionLabel) {
        if (shortcut == null || optionLabel == null) return false;
        String token = shortcut.trim();
        String normalized = optionLabel.trim().replaceFirst(
                "(?i)\\s*[（(](recommended|推荐)[）)]\\s*$", "").trim();
        if (token.equalsIgnoreCase(normalized)) return true;
        if (token.isEmpty()
                || normalized.length() <= token.length()
                || !normalized.regionMatches(true, 0, token, 0, token.length())) return false;
        char boundary = normalized.charAt(token.length());
        return Character.isWhitespace(boundary)
                || "（([【.:：、-—".indexOf(boundary) >= 0;
    }
}
