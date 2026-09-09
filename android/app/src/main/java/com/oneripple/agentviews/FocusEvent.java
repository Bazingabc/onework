package com.oneripple.agentviews;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

final class FocusEvent {
    enum Kind {
        ERROR,
        REQUIRED,
        POSSIBLE,
        NEW_RUNNING
    }

    final Kind kind;
    final String sessionId;
    final String title;
    final String messageId;
    final long updatedAt;

    private FocusEvent(Kind kind, SessionItem item) {
        this.kind = kind;
        this.sessionId = item.id;
        this.title = item.title;
        this.messageId = item.attentionMessageId;
        this.updatedAt = item.updatedAt;
    }

    static List<FocusEvent> between(
            List<SessionItem> before,
            List<SessionItem> after,
            boolean hasBaseline) {
        List<FocusEvent> events = new ArrayList<>();
        if (!hasBaseline) return events;
        Map<String, SessionItem> previous = DashboardState.index(before);

        for (SessionItem current : after) {
            SessionItem old = previous.get(current.id);
            if (current.isError() && (old == null || !old.isError())) {
                events.add(new FocusEvent(Kind.ERROR, current));
            } else if (current.needsAttention() && changedAttention(old, current, true)) {
                events.add(new FocusEvent(Kind.REQUIRED, current));
            } else if (current.possiblyNeedsAttention()
                    && changedAttention(old, current, false)) {
                events.add(new FocusEvent(Kind.POSSIBLE, current));
            } else if (current.isRunning() && (old == null || !old.isRunning())) {
                events.add(new FocusEvent(Kind.NEW_RUNNING, current));
            }
        }
        events.sort(Comparator
                .comparingInt(FocusEvent::priority)
                .thenComparing((left, right) -> Long.compare(right.updatedAt, left.updatedAt))
                .thenComparing(event -> event.sessionId));
        return events;
    }

    private static boolean changedAttention(
            SessionItem old,
            SessionItem current,
            boolean required) {
        boolean oldMatches = old != null
                && (required ? old.needsAttention() : old.possiblyNeedsAttention());
        if (!oldMatches) return true;
        return !current.attentionMessageId.isEmpty()
                && !current.attentionMessageId.equals(old.attentionMessageId);
    }

    int priority() {
        switch (kind) {
            case ERROR: return 1;
            case REQUIRED: return 2;
            case POSSIBLE: return 3;
            default: return 4;
        }
    }

    boolean isStillValid(SessionItem item) {
        if (item == null) return false;
        switch (kind) {
            case ERROR:
                return item.isError();
            case REQUIRED:
                return item.needsAttention() && sameMessageIfKnown(item);
            case POSSIBLE:
                return item.possiblyNeedsAttention() && sameMessageIfKnown(item);
            case NEW_RUNNING:
                return item.isRunning();
            default:
                return false;
        }
    }

    private boolean sameMessageIfKnown(SessionItem item) {
        return messageId.isEmpty() || messageId.equals(item.attentionMessageId);
    }
}
