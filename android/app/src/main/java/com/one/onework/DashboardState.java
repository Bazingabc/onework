package com.one.onework;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

final class DashboardState {
    static final long RECENT_COMPLETION_MS = 10 * 60 * 1_000L;

    final List<SessionItem> errors;
    final List<SessionItem> attention;
    final List<SessionItem> possible;
    final List<SessionItem> running;
    final List<SessionItem> completed;
    final int recentCompletionCount;

    private DashboardState(
            List<SessionItem> errors,
            List<SessionItem> attention,
            List<SessionItem> possible,
            List<SessionItem> running,
            List<SessionItem> completed,
            int recentCompletionCount) {
        this.errors = Collections.unmodifiableList(errors);
        this.attention = Collections.unmodifiableList(attention);
        this.possible = Collections.unmodifiableList(possible);
        this.running = Collections.unmodifiableList(running);
        this.completed = Collections.unmodifiableList(completed);
        this.recentCompletionCount = recentCompletionCount;
    }

    static DashboardState from(
            List<SessionItem> sessions,
            Map<String, Long> waitingSince,
            Map<String, Long> completedAt,
            long nowMs) {
        List<SessionItem> errors = new ArrayList<>();
        List<SessionItem> attention = new ArrayList<>();
        List<SessionItem> possible = new ArrayList<>();
        List<SessionItem> running = new ArrayList<>();
        List<SessionItem> completed = new ArrayList<>();
        int recentCount = 0;

        for (SessionItem item : sessions) {
            if (item.isError()) {
                errors.add(item);
            } else if (item.needsAttention()) {
                attention.add(item);
            } else if (item.possiblyNeedsAttention()) {
                possible.add(item);
            } else if (item.isRunning()) {
                running.add(item);
            } else {
                completed.add(item);
                long observedCompletion = completedAt.getOrDefault(
                        item.id,
                        item.updatedAt > 0 ? item.updatedAt * 1_000L : 0L);
                if (observedCompletion > 0
                        && nowMs - observedCompletion <= RECENT_COMPLETION_MS) {
                    recentCount++;
                }
            }
        }

        Comparator<SessionItem> newestFirst = (left, right) -> {
            int time = Long.compare(right.updatedAt, left.updatedAt);
            return time != 0 ? time : left.id.compareTo(right.id);
        };
        errors.sort(newestFirst);
        running.sort(newestFirst);
        completed.sort(newestFirst);
        possible.sort(newestFirst);
        attention.sort((left, right) -> {
            int type = Integer.compare(attentionPriority(left), attentionPriority(right));
            if (type != 0) return type;
            long leftSince = waitingSince.getOrDefault(left.id, fallbackObservedAt(left, nowMs));
            long rightSince = waitingSince.getOrDefault(right.id, fallbackObservedAt(right, nowMs));
            int waited = Long.compare(leftSince, rightSince);
            return waited != 0 ? waited : newestFirst.compare(left, right);
        });
        return new DashboardState(
                errors,
                attention,
                possible,
                running,
                completed,
                recentCount);
    }

    static DashboardState empty() {
        return from(
                Collections.emptyList(),
                Collections.emptyMap(),
                Collections.emptyMap(),
                0L);
    }

    int attentionCount() {
        return attention.size();
    }

    int runningCount() {
        return running.size();
    }

    int sessionCount() {
        return errors.size()
                + attention.size()
                + possible.size()
                + running.size()
                + completed.size();
    }

    String recommendedSelection() {
        if (!errors.isEmpty()) return errors.get(0).id;
        if (!attention.isEmpty()) return attention.get(0).id;
        return null;
    }

    String initialSelection() {
        if (!errors.isEmpty()) return errors.get(0).id;
        if (!attention.isEmpty()) return attention.get(0).id;
        if (!possible.isEmpty()) return possible.get(0).id;
        if (!running.isEmpty()) return running.get(0).id;
        if (!completed.isEmpty()) return completed.get(0).id;
        return null;
    }

    String chooseSelection(
            String currentId,
            AttentionEvent event,
            boolean interactionBusy) {
        if (!interactionBusy
                && event != null
                && event.shouldFocus()
                && contains(event.sessionId)) {
            return event.sessionId;
        }
        if (contains(currentId)) return currentId;
        if (interactionBusy) return null;
        return recommendedSelection();
    }

    String nextAttention(String excludingId) {
        for (SessionItem item : errors) {
            if (!item.id.equals(excludingId)) return item.id;
        }
        for (SessionItem item : attention) {
            if (!item.id.equals(excludingId)) return item.id;
        }
        for (SessionItem item : possible) {
            if (!item.id.equals(excludingId)) return item.id;
        }
        return null;
    }

    boolean contains(String id) {
        if (id == null) return false;
        return find(id) != null;
    }

    SessionItem find(String id) {
        if (id == null) return null;
        for (List<SessionItem> group : groups()) {
            for (SessionItem item : group) {
                if (id.equals(item.id)) return item;
            }
        }
        return null;
    }

    private List<List<SessionItem>> groups() {
        List<List<SessionItem>> groups = new ArrayList<>();
        groups.add(errors);
        groups.add(attention);
        groups.add(possible);
        groups.add(running);
        groups.add(completed);
        return groups;
    }

    private static int attentionPriority(SessionItem item) {
        if ("waiting_approval".equals(item.statusType)) return 0;
        return "required_protocol".equals(item.attentionType) ? 1 : 2;
    }

    private static long fallbackObservedAt(SessionItem item, long nowMs) {
        return item.updatedAt > 0 ? item.updatedAt * 1_000L : nowMs;
    }

    static Map<String, SessionItem> index(List<SessionItem> sessions) {
        Map<String, SessionItem> result = new HashMap<>();
        for (SessionItem item : sessions) result.put(item.id, item);
        return result;
    }
}
