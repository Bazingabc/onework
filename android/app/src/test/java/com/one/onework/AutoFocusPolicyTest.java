package com.one.onework;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class AutoFocusPolicyTest {
    private static SessionItem item(String id, String status, long updatedAt) {
        return new SessionItem(id, id, "managed", status, updatedAt);
    }

    private static SessionItem possible(String id, long updatedAt) {
        return new SessionItem(
                id,
                id,
                "possible_input",
                "possible_inferred",
                "attachable",
                "settled",
                updatedAt,
                "question-" + updatedAt);
    }

    private static DashboardState dashboard(SessionItem... items) {
        return DashboardState.from(
                Arrays.asList(items),
                Collections.emptyMap(),
                Collections.emptyMap(),
                100_000L);
    }

    private static List<FocusEvent> changes(
            List<SessionItem> before,
            SessionItem... after) {
        return FocusEvent.between(before, Arrays.asList(after), true);
    }

    @Test
    public void defersNewRunningUntilFifteenSecondsWithoutTouch() {
        SessionItem current = item("current", "idle", 1L);
        SessionItem running = item("running", "running", 2L);
        AutoFocusPolicy policy = new AutoFocusPolicy();
        policy.onTouch(1_000L);
        policy.observe(changes(Collections.singletonList(current), current, running));
        DashboardState state = dashboard(current, running);

        assertFalse(policy.decide(state, "current", true, false, 15_999L).shouldFocus());
        AutoFocusPolicy.Decision decision =
                policy.decide(state, "current", true, false, 16_000L);

        assertEquals("running", decision.sessionId);
        assertTrue(policy.commit(decision));
        assertFalse(policy.hasPending());
    }

    @Test
    public void anotherTouchRestartsTheCooldownAndCancelsAnOldDecision() {
        SessionItem current = item("current", "idle", 1L);
        SessionItem running = item("running", "running", 2L);
        DashboardState state = dashboard(current, running);
        AutoFocusPolicy policy = new AutoFocusPolicy();
        policy.observe(changes(Collections.singletonList(current), current, running));
        AutoFocusPolicy.Decision oldDecision =
                policy.decide(state, "current", true, false, 20_000L);

        policy.onTouch(20_000L);

        assertFalse(policy.commit(oldDecision));
        assertFalse(policy.decide(state, "current", true, false, 34_999L).shouldFocus());
        assertEquals(
                "running",
                policy.decide(state, "current", true, false, 35_000L).sessionId);
    }

    @Test
    public void possibleQuestionBeatsNewRunningAndExplicitAttentionIsProtected() {
        SessionItem current = item("current", "idle", 1L);
        SessionItem running = item("running", "running", 2L);
        SessionItem possible = possible("possible", 3L);
        AutoFocusPolicy policy = new AutoFocusPolicy();
        policy.observe(changes(
                Collections.singletonList(current),
                current,
                running,
                possible));
        DashboardState state = dashboard(current, running, possible);

        assertEquals(
                "possible",
                policy.decide(state, "current", true, false, 20_000L).sessionId);

        SessionItem required = item("required", "waiting_input", 4L);
        DashboardState protectedState = dashboard(required, running, possible);
        assertFalse(policy.decide(
                protectedState,
                "required",
                true,
                false,
                20_000L).shouldFocus());
    }

    @Test
    public void staleCandidateIsDiscardedBeforeFocus() {
        SessionItem current = item("current", "idle", 1L);
        SessionItem running = item("running", "running", 2L);
        AutoFocusPolicy policy = new AutoFocusPolicy();
        policy.observe(changes(Collections.singletonList(current), current, running));

        assertFalse(policy.decide(
                dashboard(current, item("running", "idle", 3L)),
                "current",
                true,
                false,
                20_000L).shouldFocus());
        assertFalse(policy.hasPending());
    }

    @Test
    public void foregroundAndSubmissionMustBothAllowFocus() {
        SessionItem current = item("current", "idle", 1L);
        SessionItem running = item("running", "running", 2L);
        DashboardState state = dashboard(current, running);
        AutoFocusPolicy policy = new AutoFocusPolicy();
        policy.observe(changes(Collections.singletonList(current), current, running));

        assertFalse(policy.decide(state, "current", false, false, 20_000L).shouldFocus());
        assertFalse(policy.decide(state, "current", true, true, 20_000L).shouldFocus());
        assertEquals(
                "running",
                policy.decide(state, "current", true, false, 20_000L).sessionId);
    }
}
