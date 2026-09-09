package com.oneripple.agentviews;

/** Pure release policy; distances and velocities use the same pixel coordinate space. */
final class PageSnap {
    static int target(float position, float velocity, float width, float flingThreshold) {
        if (width <= 0) return 0;
        if (Math.abs(velocity) >= flingThreshold) return velocity < 0 ? 1 : 0;
        return position <= -width * .5f ? 1 : 0;
    }
    static float clamp(float position, float width) {
        return Math.max(-Math.max(0, width), Math.min(0, position));
    }
    private PageSnap() {}
}
