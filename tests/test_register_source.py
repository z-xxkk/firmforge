"""手册拖进对话：登记进资料清单、算指纹、提取文字（DECISIONS 第 114 条）。"""

import tempfile
import tomllib
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.grounding.sources import SourceError, SourceLibrary, load_manifest, register_source


class RegisterTest(unittest.TestCase):
    def test_register_text_and_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            manifest = d / "hw" / "sources.toml"
            manifest.parent.mkdir()
            manifest.write_text("# 资料清单\n", encoding="utf-8")
            f = d / "BH1750 数据手册.txt"
            f.write_text("Power Down 0000_0000 No active state.\n", encoding="utf-8")
            doc, added = register_source(manifest, d / "raw", d / "lib", f)
            self.assertTrue(added)
            self.assertEqual((doc.kind, doc.tier), ("text", "user"))
            self.assertIn("Power Down", doc.page(1))
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual([x["id"] for x in data["doc"]], [doc.id])
            self.assertEqual(data["doc"][0]["sha256"], doc.sha256)
            self.assertTrue((d / "raw" / f.name).is_file())
            self.assertIn(doc.id, SourceLibrary.load(d / "lib").docs)
            again, added = register_source(manifest, d / "raw", d / "lib", f)      # 同一份文件：不重复登记
            self.assertFalse(added)
            self.assertEqual(again.id, doc.id)
            self.assertEqual(len(tomllib.loads(manifest.read_text(encoding="utf-8"))["doc"]), 1)
            g = d / "other" / f.name                                                 # 同名不同内容：换编号
            g.parent.mkdir()
            g.write_text("另一份", encoding="utf-8")
            doc2, added = register_source(manifest, d / "raw", d / "lib", g)
            self.assertTrue(added)
            self.assertNotEqual(doc2.id, doc.id)
            lib = load_manifest(manifest, d / "raw", d / "lib")                       # 清单能照常读回
            self.assertEqual(set(lib.docs), {doc.id, doc2.id})

    def test_rejects_unknown_type(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.exe"
            f.write_bytes(b"MZ")
            with self.assertRaises(SourceError):
                register_source(Path(d) / "s.toml", Path(d) / "raw", Path(d) / "lib", f)


if __name__ == "__main__":
    unittest.main()


import os
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class DropGuiTest(unittest.TestCase):
    def test_drop_registers_in_chat(self):
        import time
        from PySide6.QtCore import QCoreApplication, QMimeData, QUrl
        from firmforge.gui import theme as th
        from firmforge.gui.chat_page import AgentMessage, ChatPage
        from firmforge.gui.widgets import set_theme
        app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeDropTest")
        set_theme(th.current(False))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            page = ChatPage(d)
            page.hw = "hw"
            (d / "hw").mkdir()
            f = d / "bh1750.txt"
            f.write_text("Power Down 0000_0000", encoding="utf-8")
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(str(f)), QUrl.fromLocalFile(str(d / "x.exe"))])
            self.assertEqual(page._drop_files(md), [f])                          # 不认识的类型不收
            page.register_files([f])
            deadline = time.time() + 10
            while time.time() < deadline and not any("登记好了" in m.body.text() for m in page.findChildren(AgentMessage)):
                app.processEvents()
                time.sleep(0.02)
            texts = [m.body.text() for m in page.findChildren(AgentMessage)]
            self.assertTrue(any("登记好了" in t and "用户提供" in t for t in texts), texts)
            self.assertIn("bh1750", (d / "hw" / "sources.toml").read_text(encoding="utf-8"))
