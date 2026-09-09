package com.oneripple.agentviews;

/** Age uses elapsed time, so changing the device clock cannot authorize an old snapshot. */
final class SnapshotFreshness {
    static boolean sameState(SessionItem summary, SessionItem detail) {
        return summary != null && detail != null && summary.id.equals(detail.id)
                && summary.updatedAt <= detail.updatedAt
                && summary.statusType.equals(detail.statusType)
                && summary.runtimeState.equals(detail.runtimeState)
                && summary.controlType.equals(detail.controlType)
                && summary.attentionType.equals(detail.attentionType)
                && summary.attentionMessageId.equals(detail.attentionMessageId)
                && String.valueOf(summary.pending).equals(String.valueOf(detail.pending))
                && String.valueOf(summary.attention).equals(String.valueOf(detail.attention))
                && String.valueOf(summary.control).equals(String.valueOf(detail.control));
    }

    static SessionItem displayState(SessionItem summary, SessionItem detail) {
        return sameState(summary, detail) ? detail : summary;
    }

    static boolean usable(boolean stale, long ageMs, long receivedMs, long nowMs) {
        return !stale && ageMs >= 0 && nowMs >= receivedMs
                && ageMs + (nowMs - receivedMs) < 15_000;
    }
}
