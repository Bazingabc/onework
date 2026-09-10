package com.one.onework;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class AutoFocusPolicy {
    static final long COOLDOWN_MS = 15_000L;

    static final class Decision {
        static final Decision NONE = new Decision(null, null, -1L);

        final String sessionId;
        final FocusEvent event;
        private final long touchGeneration;

        private Decision(String sessionId, FocusEvent event, long touchGeneration) {
            this.sessionId = sessionId;
            this.event = event;
            this.touchGeneration = touchGeneration;
        }

        boolean shouldFocus() {
            return sessionId != null;
        }
    }

    private final Map<String, FocusEvent> pending = new LinkedHashMap<>();
    private long lastTouchAt;
    private long touchGeneration;

    synchronized void onTouch(long nowMs) {
        lastTouchAt = nowMs;
        touchGeneration++;
    }

    synchronized void observe(List<FocusEvent> events) {
        for (FocusEvent event : events) pending.put(event.sessionId, event);
    }

    synchronized Decision decide(
            DashboardState dashboard,
            String currentId,
            boolean foreground,
            boolean interactionBlocked,
            long nowMs) {
        prune(dashboard);
        if (!foreground
                || interactionBlocked
                || pending.isEmpty()
                || nowMs - lastTouchAt < COOLDOWN_MS) {
            return Decision.NONE;
        }

        FocusEvent candidate = bestCandidate(dashboard);
        if (candidate == null) return Decision.NONE;
        if (candidate.sessionId.equals(currentId)) {
            pending.remove(candidate.sessionId);
            return Decision.NONE;
        }

        SessionItem current = dashboard.find(currentId);
        if (current != null && currentPriority(current) < candidate.priority()) {
            return Decision.NONE;
        }
        return new Decision(candidate.sessionId, candidate, touchGeneration);
    }

    synchronized boolean commit(Decision decision) {
        if (!decision.shouldFocus() || decision.touchGeneration != touchGeneration) return false;
        FocusEvent current = pending.get(decision.sessionId);
        if (current != decision.event) return false;
        pending.clear();
        return true;
    }

    synchronized void consume(String sessionId) {
        if (sessionId != null) pending.remove(sessionId);
    }

    synchronized long millisUntilEligible(long nowMs) {
        if (pending.isEmpty()) return -1L;
        return Math.max(0L, COOLDOWN_MS - (nowMs - lastTouchAt));
    }

    synchronized boolean hasPending() {
        return !pending.isEmpty();
    }

    private void prune(DashboardState dashboard) {
        pending.entrySet().removeIf(entry ->
                !entry.getValue().isStillValid(dashboard.find(entry.getKey())));
    }

    private FocusEvent bestCandidate(DashboardState dashboard) {
        FocusEvent candidate = firstPending(dashboard.errors);
        if (candidate != null) return candidate;
        candidate = firstPending(dashboard.attention);
        if (candidate != null) return candidate;
        candidate = firstPending(dashboard.possible);
        if (candidate != null) return candidate;
        return firstPending(dashboard.running);
    }

    private FocusEvent firstPending(List<SessionItem> sessions) {
        for (SessionItem item : sessions) {
            FocusEvent event = pending.get(item.id);
            if (event != null && event.isStillValid(item)) return event;
        }
        return null;
    }

    private static int currentPriority(SessionItem item) {
        if (item.isError()) return 1;
        if (item.needsAttention()) return 2;
        if (item.possiblyNeedsAttention()) return 3;
        if (item.isRunning()) return 4;
        return 5;
    }
}
