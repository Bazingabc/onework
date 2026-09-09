package com.oneripple.agentviews;

import static org.junit.Assert.*;
import org.junit.Test;

public class SnapshotFreshnessTest {
    @Test public void oldApprovalCannotOverrideRunningStateAtSameTimestamp() {
        SessionItem approval = new SessionItem("one", "test", "managed", "waiting_approval", 100);
        SessionItem running = new SessionItem("one", "test", "managed", "running", 100);
        assertFalse(SnapshotFreshness.sameState(running, approval));
        assertSame(running, SnapshotFreshness.displayState(running, approval));
    }
    @Test public void newAttentionEvidenceAndControlInvalidateOldDetail() {
        SessionItem old = new SessionItem("one", "test", "waiting_input", "required_inferred", "direct", "settled", 100, "old");
        SessionItem changed = new SessionItem("one", "test", "waiting_input", "required_inferred", "direct", "settled", 100, "new");
        assertFalse(SnapshotFreshness.sameState(changed, old));
        SessionItem elsewhere = new SessionItem("one", "test", "waiting_input", "required_inferred", "busy_elsewhere", "settled", 100, "old");
        assertFalse(SnapshotFreshness.sameState(elsewhere, old));
        assertTrue(SnapshotFreshness.sameState(old, old));
    }
    @Test public void freshSnapshotExpiresWhileDisplayed() {
        assertTrue(SnapshotFreshness.usable(false, 4_000, 100, 200));
        assertFalse(SnapshotFreshness.usable(false, 4_000, 100, 11_100));
    }
    @Test public void staleMissingOrReversedAgeCannotAuthorize() {
        assertFalse(SnapshotFreshness.usable(true, 0, 100, 100));
        assertFalse(SnapshotFreshness.usable(false, -1, 100, 100));
        assertFalse(SnapshotFreshness.usable(false, 0, 100, 99));
    }
}
