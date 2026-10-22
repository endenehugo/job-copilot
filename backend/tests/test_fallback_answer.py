"""兜底回答的可解释性（2026-10-22）。

线上 HR 看到的是「抱歉，我暂时无法生成回答。」——三种完全不同的失败被折叠成一句，
用户不知道是重试、拆小需求还是换模型。这里锁住"不同原因给不同建议"。
"""
import unittest

from app.services.conversation_chat_service import ConversationChatService


class FallbackAnswerTest(unittest.TestCase):
    def test_tool_chain_failure_mentions_split_request(self):
        text = ConversationChatService._fallback_answer(
            ["工具调用已达轮次上限，未能生成最终回答"]
        )
        self.assertIn("工具链没跑完", text)
        self.assertIn("拆小", text)
        # 旧文案不能再出现：它既没原因也没建议
        self.assertNotIn("抱歉，我暂时无法生成回答", text)

    def test_empty_model_output_suggests_retry(self):
        text = ConversationChatService._fallback_answer([])
        self.assertIn("瞬时故障", text)
        self.assertIn("重试", text)
        self.assertNotIn("工具链", text)

    def test_two_branches_are_distinct(self):
        self.assertNotEqual(
            ConversationChatService._fallback_answer(["工具执行失败"]),
            ConversationChatService._fallback_answer([]),
        )


if __name__ == "__main__":
    unittest.main()
