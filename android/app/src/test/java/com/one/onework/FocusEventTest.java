package com.one.onework;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class FocusEventTest {
    private static SessionItem item(String id, String status, long updatedAt) {
        return new SessionItem(id, id, "managed", status, updatedAt);
    }

    private static SessionItem possible(String id, String messageId, long updatedAt) {
        return new SessionItem(
                id,
                id,
                "possible_input",
                "possible_inferred",
                "attachable",
                "settled",
                updatedAt,
                messageId);
    }

    @Test
    public void initialSnapshotDoesNotCreateFocusEvents() {
        List<FocusEvent> events = FocusEvent.between(
                Collections.emptyList(),
                Collections.singletonList(item("new", "running", 10L)),
                false);

        assertTrue(events.isEmpty());
    }

    @Test
    public void detectsNewRunningSessionAndPossibleTransition() {
        SessionItem stable = item("stable", "idle", 10L);
        SessionItem oldQuestion = item("question", "running", 11L);
        List<FocusEvent> events = FocusEvent.between(
                Arrays.asList(stable, oldQuestion),
                Arrays.asList(
                        stable,
                        possible("question", "question-1", 13L),
                        item("new", "running", 12L)),
                true);

        assertEquals(2, events.size());
        assertEquals(FocusEvent.Kind.POSSIBLE, events.get(0).kind);
        assertEquals("question", events.get(0).sessionId);
        assertEquals(FocusEvent.Kind.NEW_RUNNING, events.get(1).kind);
    }

    @Test
    public void changedPossibleQuestionCreatesAnotherEvent() {
        List<FocusEvent> events = FocusEvent.between(
                Collections.singletonList(possible("question", "old", 10L)),
                Collections.singletonList(possible("question", "new", 11L)),
                true);

        assertEquals(1, events.size());
        assertEquals(FocusEvent.Kind.POSSIBLE, events.get(0).kind);
    }

    @Test
    public void existingSessionReturningToRunningCreatesFocusEvent() {
        List<FocusEvent> events = FocusEvent.between(
                Collections.singletonList(item("active", "waiting_approval", 10L)),
                Collections.singletonList(item("active", "running", 11L)),
                true);

        assertEquals(1, events.size());
        assertEquals(FocusEvent.Kind.NEW_RUNNING, events.get(0).kind);
        assertEquals("active", events.get(0).sessionId);
    }
}
