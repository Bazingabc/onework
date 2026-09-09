package com.oneripple.agentviews;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

public class DashboardStateTest {
    private static SessionItem item(
            String id,
            String ownership,
            String status,
            long updatedAt) {
        return new SessionItem(id, id, ownership, status, updatedAt);
    }

    @Test
    public void groupsEverySessionByAttentionInsteadOfSource() {
        long now = 2_000_000L;
        DashboardState state = DashboardState.from(
                Arrays.asList(
                        item("approval", "managed", "waiting_approval", 1_000),
                        item("input", "managed", "waiting_input", 1_100),
                        item("running", "managed", "running", 1_200),
                        item("idle", "managed", "idle", 1_300),
                        item("error", "managed", "error", 1_400),
                        new SessionItem(
                                "possible",
                                "possible",
                                "possible_input",
                                "possible_inferred",
                                "attachable",
                                "settled",
                                1_500)),
                Collections.emptyMap(),
                Collections.emptyMap(),
                now);

        assertEquals(1, state.errors.size());
        assertEquals(2, state.attentionCount());
        assertEquals(1, state.possible.size());
        assertEquals(1, state.runningCount());
        assertEquals(1, state.completed.size());
        assertEquals(6, state.sessionCount());
        assertTrue(state.possible.get(0).isWritable(true));
        assertTrue(state.attention.get(0).hasAttentionCard());
    }

    @Test
    public void prioritizesApprovalThenLongestWaitingInput() {
        SessionItem recentApproval = item("approval", "managed", "waiting_approval", 1_900);
        SessionItem olderInput = item("older", "managed", "waiting_input", 1_100);
        SessionItem newerInput = item("newer", "managed", "waiting_input", 1_800);
        Map<String, Long> waitingSince = new HashMap<>();
        waitingSince.put("approval", 1_800_000L);
        waitingSince.put("older", 1_000_000L);
        waitingSince.put("newer", 1_700_000L);

        DashboardState state = DashboardState.from(
                Arrays.asList(newerInput, olderInput, recentApproval),
                waitingSince,
                Collections.emptyMap(),
                2_000_000L);

        assertEquals("approval", state.attention.get(0).id);
        assertEquals("older", state.attention.get(1).id);
        assertEquals("newer", state.attention.get(2).id);
    }

    @Test
    public void recommendsActiveSessionForEmptyPaneButNotForAttentionFocus() {
        long now = 2_000_000L;
        Map<String, Long> completedAt = new HashMap<>();
        completedAt.put("recent", now - 1_000L);
        completedAt.put("old", now - DashboardState.RECENT_COMPLETION_MS - 1L);
        DashboardState state = DashboardState.from(
                Arrays.asList(
                        item("running", "managed", "running", 1_900),
                        item("recent", "managed", "idle", 1_800),
                        item("old", "managed", "idle", 1_700)),
                Collections.emptyMap(),
                completedAt,
                now);

        assertEquals(null, state.recommendedSelection());
        assertEquals("running", state.initialSelection());
        assertEquals(1, state.recentCompletionCount);
        assertTrue(state.contains("old"));
    }

    @Test
    public void emptyPaneSelectionUsesAttentionPriorityThenActivity() {
        DashboardState state = DashboardState.from(
                Arrays.asList(
                        item("running", "managed", "running", 2_000),
                        item("input", "managed", "waiting_input", 1_900),
                        item("approval", "managed", "waiting_approval", 1_800),
                        item("error", "managed", "error", 1_700)),
                Collections.emptyMap(),
                Collections.emptyMap(),
                2_000_000L);

        assertEquals("error", state.initialSelection());
        assertEquals("error", state.recommendedSelection());
    }

    @Test
    public void newBlockerOnlyStealsFocusWhenUserIsNotEditing() {
        SessionItem current = item("current", "managed", "running", 1_900);
        SessionItem waiting = item("waiting", "managed", "waiting_input", 2_000);
        DashboardState state = DashboardState.from(
                Arrays.asList(current, waiting),
                Collections.emptyMap(),
                Collections.emptyMap(),
                2_000_000L);
        AttentionEvent event = AttentionEvent.between(
                Arrays.asList(current, item("waiting", "managed", "running", 1_800)),
                Arrays.asList(current, waiting),
                true);

        assertEquals("waiting", state.chooseSelection("current", event, false));
        assertEquals("current", state.chooseSelection("current", event, true));
        assertEquals(null, state.chooseSelection("missing", event, true));
    }
}
