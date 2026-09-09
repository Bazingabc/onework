package com.oneripple.agentviews;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class SessionDraftsTest {
    @Test
    public void keepsDraftsIsolatedBySessionAndClearsOnlyTheSentOne() {
        SessionDrafts drafts = new SessionDrafts();
        drafts.save("one", "reply one");
        drafts.save("two", "reply two");

        assertEquals("reply one", drafts.get("one"));
        assertEquals("reply two", drafts.get("two"));

        drafts.clear("one");

        assertEquals("", drafts.get("one"));
        assertEquals("reply two", drafts.get("two"));
    }
}
