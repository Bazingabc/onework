package com.one.onework;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

public class AttentionEventTest {
    private static SessionItem item(String id, String status) {
        return new SessionItem(id, id, "managed", status, 1_000L);
    }

    private static SessionItem attentionItem(String messageId) {
        return new SessionItem(
                "one",
                "one",
                "waiting_input",
                "required_inferred",
                "attachable",
                "settled",
                1_000L,
                messageId);
    }

    @Test
    public void initialSnapshotDoesNotPretendExistingWaitIsNew() {
        AttentionEvent event = AttentionEvent.between(
                Collections.emptyList(),
                Collections.singletonList(item("one", "waiting_input")),
                false);

        assertFalse(event.isVisible());
    }

    @Test
    public void detectsAndFocusesNewHumanIntervention() {
        AttentionEvent event = AttentionEvent.between(
                Collections.singletonList(item("one", "running")),
                Collections.singletonList(item("one", "waiting_input")),
                true);

        assertEquals(AttentionEvent.Kind.NEW_ATTENTION, event.kind);
        assertEquals("one", event.sessionId);
        assertTrue(event.shouldFocus());
        assertEquals(AttentionEvent.Tone.ATTENTION, event.tone);
    }

    @Test
    public void detectsAChangedQuestionInTheSameWaitingSession() {
        AttentionEvent event = AttentionEvent.between(
                Collections.singletonList(attentionItem("message-old")),
                Collections.singletonList(attentionItem("message-new")),
                true);
        AttentionEvent unchanged = AttentionEvent.between(
                Collections.singletonList(attentionItem("message-new")),
                Collections.singletonList(attentionItem("message-new")),
                true);

        assertEquals(AttentionEvent.Kind.NEW_ATTENTION, event.kind);
        assertFalse(unchanged.isVisible());
    }

    @Test
    public void reportsPossibleQuestionAndNewRunningSession() {
        SessionItem stable = item("stable", "idle");
        SessionItem possible = new SessionItem(
                "possible",
                "possible",
                "possible_input",
                "possible_inferred",
                "attachable",
                "settled",
                2_000L,
                "question");
        AttentionEvent possibleEvent = AttentionEvent.between(
                Arrays.asList(stable, item("possible", "running")),
                Arrays.asList(stable, possible),
                true);
        AttentionEvent runningEvent = AttentionEvent.between(
                Collections.singletonList(stable),
                Arrays.asList(stable, item("new", "running")),
                true);

        assertEquals(AttentionEvent.Kind.NEW_POSSIBLE, possibleEvent.kind);
        assertEquals(AttentionEvent.Tone.POSSIBLE, possibleEvent.tone);
        assertEquals(AttentionEvent.Kind.NEW_RUNNING, runningEvent.kind);
        assertEquals(AttentionEvent.Tone.RUNNING, runningEvent.tone);
    }

    @Test
    public void errorWinsWhenSeveralStatesChangeTogether() {
        AttentionEvent event = AttentionEvent.between(
                Arrays.asList(item("question", "running"), item("broken", "running")),
                Arrays.asList(item("question", "waiting_input"), item("broken", "error")),
                true);

        assertEquals(AttentionEvent.Kind.ERROR, event.kind);
        assertEquals("broken", event.sessionId);
        assertTrue(event.persistent);
    }

    @Test
    public void reportsCompletionWithoutStealingAttention() {
        AttentionEvent event = AttentionEvent.between(
                Collections.singletonList(item("one", "running")),
                Collections.singletonList(item("one", "idle")),
                true);

        assertEquals(AttentionEvent.Kind.COMPLETED, event.kind);
        assertFalse(event.shouldFocus());
        assertEquals(AttentionEvent.Tone.SUCCESS, event.tone);
    }
}
