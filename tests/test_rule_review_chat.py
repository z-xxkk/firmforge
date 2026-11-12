"""规则在对话里就地确认（DECISIONS 第 114 条）：规则、确认要点、手册原页在同一张卡片里，全部表态才能提交。"""

import os
import shutil
import unittest
from pathlib import Path

import helpers
from firmforge.gui.rule_review import review_points

RULE = {"id": "R-GPIO-001", "kind": "pin", "scope": ["gpio"], "tier": "official_manual", "status": "pending",
        "text": "GPIO12–GPIO17 接封装内 SPI Flash，不要分配给外设",
        "cite": {"doc": "esp32c3-ds", "page": 23, "section": "2.3.4",
                 "quote": "GPIO12, GPIO13, GPIO14, GPIO15, GPIO16, GPIO17: SPI0/1 interface connected to the in-package flash"},
        "steps": [["1", "ok", "资料已登记"], ["2", "ok", "引用原文在第 23 页"], ["3", "unknown", "语义没有核对"]]}
RULE2 = {**RULE, "id": "R-X", "tier": "user", "cite": {"doc": "notes", "page": None, "quote": "x"}, "steps": [],
         "reasons": ["引用找不到"]}


class PointsTest(unittest.TestCase):
    def test_points(self):
        p = review_points(RULE)
        self.assertTrue(p[0].startswith("判定器没能确认：语义没有核对"))     # 没下结论的步骤排最前
        self.assertTrue(any("适用范围" in x for x in p))
        p2 = review_points(RULE2)
        self.assertTrue(any("多次被退回" in x for x in p2))
        self.assertTrue(any("不是官方资料" in x for x in p2))


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class ChatReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeRuleReviewTest")
        set_theme(th.current(False))

    def test_review_in_chat(self):
        from firmforge.gui.chat_page import ChatPage
        from firmforge.gui.rule_review import RuleReview

        class Backend:
            got = None

            def answer(self, v):
                Backend.got = v
        page = ChatPage(Path(helpers.ROOT))
        page.hw = "hardware/esp32c3_supermini"
        page.backend = Backend()
        page.on_ask("rules", {"pending": [RULE], "escalated": [RULE2]})
        rv = page.findChildren(RuleReview)
        self.assertEqual(len(rv), 1)
        rv = rv[0]
        self.assertEqual(len(rv.cards), 2)
        self.assertFalse(rv.submit_btn.isEnabled())
        rv.cards[0].decide("approve")
        self.assertFalse(rv.cards[1].decide("reject"))                   # 驳回没写理由：不算表态
        self.assertFalse(rv.submit_btn.isEnabled())
        rv.cards[1].comment.setText("引用不对")
        rv.cards[1].decide("reject")
        self.assertTrue(rv.submit_btn.isEnabled())
        rv.submit()
        self.assertEqual(Backend.got, {"R-GPIO-001": ("approve", ""), "R-X": ("reject", "引用不对")})
        self.assertIsNone(page.pending)

    @unittest.skipUnless(shutil.which("pdftoppm") and (Path(helpers.ROOT) / ".ff_sources/raw/esp32-c3_datasheet_en.pdf").is_file(),
                         "没有 poppler 或手册原件")
    def test_page_shown_beside_rule(self):
        from firmforge.gui.rule_review import RuleReview
        rv = RuleReview({"pending": [RULE]}, Path(helpers.ROOT), "hardware/esp32c3_supermini")
        rv._next_page()
        card = rv.cards[0]
        self.assertTrue(card.pic.isVisible() or not card.pic.isHidden())
        self.assertIn("绿框", card.page_note.text())


if __name__ == "__main__":
    unittest.main()
