package com.oneripple.agentviews;

import java.util.HashMap;
import java.util.Map;

final class SessionDrafts {
    private final Map<String, String> values = new HashMap<>();

    void save(String sessionId, String value) {
        if (sessionId == null) return;
        String safeValue = value == null ? "" : value;
        if (safeValue.isEmpty()) values.remove(sessionId);
        else values.put(sessionId, safeValue);
    }

    String get(String sessionId) {
        if (sessionId == null) return "";
        return values.getOrDefault(sessionId, "");
    }

    void clear(String sessionId) {
        if (sessionId != null) values.remove(sessionId);
    }
}
