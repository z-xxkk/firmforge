"""给桌面软件的 4 个工作区截图（开发用；不联网，用假模型造一次真实的运行再回放）。

在 firmforge-gui 镜像里运行（offscreen）：
    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work -e HOME=/tmp \
        --entrypoint python3 firmforge-gui scripts/gui_screenshots.py docs/screenshots
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main(out: Path) -> None:
    from test_multi_agent import B3, RB, SETTINGS, diag, rb_team
    from firmforge.gui.app import MainWindow
    from firmforge.llm import FakeLLM
    from firmforge.orchestrator.engine import run_multi

    app = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("FirmForgeShots")
    QSettings().clear()
    out.mkdir(parents=True, exist_ok=True)
    # 让模块 c 第一次交卷有错，第二次改好，这样对话里有失败、诊断、修正
    from test_multi_agent import RB_CODE, block
    bad = block("src/glue.h", "#ifndef GLUE_H\n#define GLUE_H\n#include \"ringbuf.h\"\nsize_t glue_total(const ringbuf_t *rb, int extra);\n#endif\n") \
        + block("src/glue.c", '#include "glue.h"\n#include "util.h"\nsize_t glue_total(const ringbuf_t *rb, int extra) { (void)extra; return ringbuf_count(rb); }\n')
    team = rb_team(coder=lambda m, msgs, n: bad if (m == "c" and n == 1) else RB_CODE[m],
                   diagnoser=lambda m, msgs, n: diag("code", "glue_total 忘了加上 extra", "返回 util_add(count, extra)"))
    with tempfile.TemporaryDirectory() as d:
        res = run_multi(RB, B3, llm=FakeLLM(team), settings=SETTINGS, runs_root=d)
        w = MainWindow(ROOT)
        w.apply_theme()
        w.resize(1440, 900)
        w.show()
        page = w.chat
        page.add_user("帮我写一个 STM32F103 上的环形缓冲区，带一个统计模块。")
        page.add_agent("analyst", "analyst", "需求文档和公共头文件整理好了。缓冲区大小在初始化时传入，这一点是我推断的，不对请改。")
        page.start_replay(res.run_dir, append=True)
        page.replay.seek(len(page.replay.events))
        page.title.setText("环形缓冲区")
        app.processEvents()
        page.select_module("c")
        page.tabs.setCurrentIndex(3)
        for i, name in enumerate(["chat", "designer", "team", "settings"]):
            w.go(i)
            if name == "designer":
                w.designer.select("repair")
            if name == "settings":
                w.settings.nav.setCurrentRow(1)
            for _ in range(5):
                app.processEvents()
            w.grab().save(str(out / f"gui_{name}.png"))
            print("已保存", out / f"gui_{name}.png")
        w.close()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"))
