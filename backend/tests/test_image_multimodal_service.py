import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.conversation_chat_service import ConversationChatService
from app.services.conversation_store_service import ConversationStoreService
from app.utils import ResourceUtils


class TestImageMultimodalService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="job-copilot-image-test-")
        ResourceUtils.init(os.path.join(self.temp_dir, "resources"))
        self.service = ConversationChatService(
            conversation_store_service=MagicMock(),
            document_index_service=MagicMock(),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_compose_user_content_with_images(self):
        content = self.service._compose_user_content(
            "请描述图片内容",
            ["/conversation/image/conv_1/img_1.png", "/conversation/image/conv_1/img_2.png"],
        )
        self.assertIn("![image](/conversation/image/conv_1/img_1.png)", content)
        self.assertTrue(content.endswith("请描述图片内容"))

    def test_build_history_strips_image_markdown_for_text_mode(self):
        history = [{
            "role": "user",
            "content": "![image](/conversation/image/conv_1/img_1.png)\n请描述图片内容",
        }]
        messages = self.service._build_history(history)
        self.assertEqual(messages[0].content, "请描述图片内容")

    def test_resolve_image_path(self):
        image_dir = ResourceUtils.ensure_resource_dir(os.path.join("uploads", "images", "conv_test"))
        image_path = os.path.join(image_dir, "img_test.png")
        png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc```\x00\x00\x00\x04\x00\x01\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82'
        with open(image_path, "wb") as file_obj:
            file_obj.write(png_data)

        resolved = self.service._resolve_image_path("/conversation/image/conv_test/img_test.png")
        self.assertTrue(resolved.endswith("img_test.png"))

    def test_extract_multimodal_text_from_list(self):
        # DashScope 多模态返回的 content 格式
        content = [{"text": "图片中显示 1+1="}, {"text": "答案是 2"}]
        result = self.service._extract_multimodal_text(content)
        self.assertEqual(result, "图片中显示 1+1=答案是 2")

    def test_extract_multimodal_text_from_string(self):
        result = self.service._extract_multimodal_text("直接返回的文本")
        self.assertEqual(result, "直接返回的文本")

    def test_store_service_strip_image_markdown(self):
        cleaned = ConversationStoreService._strip_image_markdown(
            "![image](/conversation/image/conv_1/img_1.png)\n这是一张截图"
        ).strip()
        self.assertEqual(cleaned, "这是一张截图")


if __name__ == "__main__":
    unittest.main()