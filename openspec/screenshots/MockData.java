package com.oneripple.agentviews;

import org.json.JSONArray;
import org.json.JSONObject;

/** Documentation fixtures only. Never included by the production Gradle project. */
final class MockData {
    private static final long NOW = System.currentTimeMillis() / 1000;
    private static final String QUESTION = "离线缓存方案已就绪，先实现哪种范围？\nA：最近一小时　B：今天全部任务";

    static JSONObject response(String method, String path) throws Exception {
        if (!"GET".equals(method)) throw new IllegalStateException("演示模式不执行任何操作");
        if (path.equals("/api/health")) return new JSONObject().put("ok", true);
        if (path.equals("/api/sessions")) return new JSONObject().put("sessions", sessions()).put("stale", false);
        if (path.startsWith("/api/sessions/")) {
            String id = path.substring("/api/sessions/".length());
            JSONArray values = sessions();
            for (int i = 0; i < values.length(); i++) {
                JSONObject item = values.getJSONObject(i);
                if (item.getString("id").equals(id)) {
                    item.put("messages", new JSONArray()
                        .put(new JSONObject().put("role", "user").put("text", "请为示例工作台设计离线缓存，让短暂断网时仍能查看任务。"))
                        .put(new JSONObject().put("role", "assistant").put("text", QUESTION)));
                    return new JSONObject().put("session", item).put("revision", "demo-revision")
                        .put("stale", false).put("snapshot", new JSONObject().put("ageMs", 0));
                }
            }
        }
        if (path.equals("/api/work/tasks")) {
            JSONArray rows = new JSONArray();
            String[] titles = {"评审工作台离线缓存方案", "补充扫码配对的异常提示", "整理首批开发者试用反馈", "准备周五的产品演示", "更新项目安装与贡献指南"};
            for (int i = 0; i < titles.length; i++) rows.put(new JSONObject()
                .put("guid", "demo-todo-" + i).put("revision", "demo")
                .put("summary", titles[i]).put("due_at", ""));
            return new JSONObject().put("items", rows).put("updatedAt", NOW).put("error", JSONObject.NULL);
        }
        if (path.equals("/api/work/usage")) {
            JSONObject bucket = new JSONObject()
                .put("primary", new JSONObject().put("windowDurationMins", 300).put("usedPercent", 24).put("resetsAt", NOW + 7200))
                .put("secondary", new JSONObject().put("windowDurationMins", 10080).put("usedPercent", 38).put("resetsAt", NOW + 259200));
            return new JSONObject().put("available", true).put("data", new JSONObject().put("rateLimits", bucket));
        }
        throw new IllegalStateException("没有演示数据的接口：" + path);
    }

    private static JSONArray sessions() throws Exception {
        JSONArray result = new JSONArray();
        String[][] rows = {
            {"demo-cache", "工作台离线缓存方案", "waiting_input", "等你回复", "settled"},
            {"demo-pairing", "优化扫码配对的错误提示", "running", "运行中", "running"},
            {"demo-tests", "补充任务状态转换测试", "running", "运行中", "running"},
            {"demo-docs", "整理开发者快速开始指南", "idle", "已完成", "settled"}
        };
        for (int i = 0; i < rows.length; i++) {
            String[] row = rows[i];
            JSONObject attention = new JSONObject().put("type", i == 0 ? "required_inferred" : "none");
            if (i == 0) attention.put("question", QUESTION).put("responseMode", "text")
                .put("messageId", "demo-message").put("options", new JSONArray().put("A").put("B"));
            result.put(new JSONObject().put("id", row[0]).put("title", row[1])
                .put("cwd", "/demo/workspace").put("source", "cli").put("ownership", "managed")
                .put("runtimeState", row[4]).put("createdAt", NOW - 1500).put("updatedAt", NOW - i * 60)
                .put("preview", i == 0 ? QUESTION : i == 3 ? "安装步骤与验证清单已整理完成。" : "正在检查实现并补充验证。")
                .put("status", new JSONObject().put("type", row[2]).put("label", row[3]))
                .put("attention", attention).put("control", new JSONObject().put("type", "direct")));
        }
        return result;
    }
}
