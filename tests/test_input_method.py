"""输入法接入（2026-11-18：负责人用 fcitx5，pip 装的 PySide6 没有 fcitx 插件，输入框打不了中文）：
QT_IM_MODULE 是 fcitx、Qt 找不到 fcitx 插件但有 ibus 插件时，改走 ibus（fcitx5 提供 IBus 兼容接口）。"""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

try:
    from firmforge.gui.app import pick_im_module
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "没装 PySide6")
class PickTest(unittest.TestCase):
    def dirs(self, d, *names):
        p = Path(d) / "plugins"
        (p / "platforminputcontexts").mkdir(parents=True, exist_ok=True)
        for n in names:
            (p / "platforminputcontexts" / n).write_text("", encoding="utf-8")
        return [p]

    def test_cases(self):
        ibus, fcitx = "libibusplatforminputcontextplugin.so", "libfcitx5platforminputcontextplugin.so"
        with tempfile.TemporaryDirectory() as d:
            only_ibus = self.dirs(d, ibus)
            self.assertEqual(pick_im_module({"QT_IM_MODULE": "fcitx"}, only_ibus), "ibus")
            self.assertEqual(pick_im_module({"QT_IM_MODULE": "fcitx5"}, only_ibus), "ibus")
            self.assertIsNone(pick_im_module({"QT_IM_MODULE": "ibus"}, only_ibus))      # 本来就是 ibus：不动
            self.assertIsNone(pick_im_module({}, only_ibus))                            # 没设：不动
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pick_im_module({"QT_IM_MODULE": "fcitx"}, self.dirs(d, ibus, fcitx)))   # 有 fcitx 插件：不动
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pick_im_module({"QT_IM_MODULE": "fcitx"}, self.dirs(d)))  # 两种都没有：不动
