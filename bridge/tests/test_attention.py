import unittest

from agent_views.bridge.attention import classify_agent_message


class AttentionClassifierTest(unittest.TestCase):
    def test_direct_free_text_request_is_required(self):
        result = classify_agent_message(
            "方案已收敛。\n\n请描述一下你工作时平板通常如何摆放，以及是否长期保持前台。",
            message_id="a-1",
        )

        self.assertEqual(result["type"], "required_inferred")
        self.assertEqual(result["responseMode"], "free_text")
        self.assertEqual(result["messageId"], "a-1")
        self.assertIn("请描述", result["question"])

    def test_choice_and_confirmation_modes_are_extracted(self):
        choice = classify_agent_message("请选择 1 / 2 / A / B 中的一项。")
        confirmation = classify_agent_message("请确认；如果方向无误，回复“按此实现”。")
        concise_choice = classify_agent_message("选 A / B 即可。")
        plain_confirmation = classify_agent_message("可以按此执行吗？")

        self.assertEqual(choice["type"], "required_inferred")
        self.assertEqual(choice["responseMode"], "choice")
        self.assertEqual(choice["options"], ["1", "2", "A", "B"])
        self.assertEqual(confirmation["responseMode"], "confirmation")
        self.assertEqual(concise_choice["type"], "required_inferred")
        self.assertEqual(plain_confirmation["type"], "required_inferred")

    def test_question_without_direct_request_is_only_possible(self):
        result = classify_agent_message("还有没有遗漏的边界条件？")

        self.assertEqual(result["type"], "possible_inferred")

    def test_commentary_code_quotes_and_closed_results_do_not_alert(self):
        commentary = classify_agent_message("请确认接下来怎么做？", phase="commentary")
        code = classify_agent_message("已完成。\n\n```text\n请选择 A？\n```\n无需操作。")
        quote = classify_agent_message("> 请描述需求？\n\n分析已经完成，无需回复。")
        completed_choice = classify_agent_message("已选择 1（继续）。")
        heading = classify_agent_message("# 请确认是否继续？\n\n以下是方案说明。")

        self.assertEqual(commentary["type"], "none")
        self.assertEqual(code["type"], "none")
        self.assertEqual(quote["type"], "none")
        self.assertEqual(completed_choice["type"], "none")
        self.assertEqual(heading["type"], "none")

    def test_legacy_message_requires_a_settled_turn(self):
        active = classify_agent_message(
            "请提供下一步选择。", phase=None, turn_status="inProgress"
        )
        active_final = classify_agent_message(
            "请提供下一步选择。", phase="final_answer", turn_status="inProgress"
        )
        settled = classify_agent_message(
            "请提供下一步选择。", phase=None, turn_status="completed"
        )

        self.assertEqual(active["type"], "none")
        self.assertEqual(active_final["type"], "none")
        self.assertEqual(settled["type"], "required_inferred")


if __name__ == "__main__":
    unittest.main()
