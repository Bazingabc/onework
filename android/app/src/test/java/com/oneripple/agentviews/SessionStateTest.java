package com.oneripple.agentviews;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class SessionStateTest {
    @Test
    public void labelsProtocolStatesInChinese() {
        assertEquals("运行中", SessionState.fallbackLabel("running"));
        assertEquals("等待输入", SessionState.fallbackLabel("waiting_input"));
        assertEquals("可能需要你", SessionState.fallbackLabel("possible_input"));
        assertEquals("状态待确认", SessionState.fallbackLabel("unknown"));
    }

    @Test
    public void directAndAttachableHealthySessionsAreWritable() {
        assertTrue(SessionState.isWritable("direct", "idle", true));
        assertTrue(SessionState.isWritable("attachable", "waiting_input", true));
        assertFalse(SessionState.isWritable("busy_elsewhere", "idle", true));
        assertFalse(SessionState.isWritable("direct", "idle", false));
        assertFalse(SessionState.isWritable("direct", "error", true));
    }

    @Test
    public void shortcutMatchesRecommendedOptionLabels() {
        assertTrue(SessionState.shortcutMatches("1", "1 (Recommended)"));
        assertTrue(SessionState.shortcutMatches("a", "A（推荐）"));
        assertTrue(SessionState.shortcutMatches("1", "1（继续）(Recommended)"));
        assertTrue(SessionState.shortcutMatches("A", "A. 采用方案"));
        assertFalse(SessionState.shortcutMatches("1", "2"));
        assertFalse(SessionState.shortcutMatches("1", "10（稍后）"));
    }
}
