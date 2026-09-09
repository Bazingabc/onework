package com.oneripple.agentviews;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class PageSnapTest {
    @Test public void releaseUsesPositionUnlessFlinging() {
        assertEquals(0, PageSnap.target(-200, 0, 1000, 550));
        assertEquals(1, PageSnap.target(-700, 0, 1000, 550));
        assertEquals(1, PageSnap.target(-100, -800, 1000, 550));
        assertEquals(0, PageSnap.target(-900, 800, 1000, 550));
    }
    @Test public void reverseDirectionAndExactThresholdAreDeterministic() {
        assertEquals(1, PageSnap.target(-500, 0, 1000, 550));
        assertEquals(0, PageSnap.target(-499, 0, 1000, 550));
        assertEquals(0, PageSnap.target(-800, 550, 1000, 550));
        assertEquals(0, PageSnap.target(-800, -550, 0, 550));
    }
    @Test public void dragNeverExposesEmptyCanvasBeyondEitherEnd() {
        assertEquals(0, PageSnap.clamp(100, 1000), 0);
        assertEquals(-1000, PageSnap.clamp(-1200, 1000), 0);
        assertEquals(-350, PageSnap.clamp(-350, 1000), 0);
    }
}
