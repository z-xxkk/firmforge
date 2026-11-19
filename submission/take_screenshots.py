"""给方案书和 PPT 重拍界面截图（离屏渲染真实的桌面软件，打开真实的运行记录；不调用模型、不花钱）。

用法：QT_QPA_PLATFORM=offscreen .venv/bin/python submission/take_screenshots.py
输出：submission/screenshots/ 下 01–16 号截图（1600×960，深色主题）。

用到的运行记录：
- 主线：.ff_runs/20261119-235548-gui（演示视频那次真跑：MPU6050 + 片内温度 + NVS，全部通过）
- 带传感器的工程图、接线图：.ff_runs/20261116-025839-eval-mpu6050_drdy-full-2
- 没通过的讲解：20261116-140631-gui（ADC 4 次没通过那次；已被移到回收站时从回收站只读）
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

MAIN = ROOT / ".ff_runs/20261119-235548-gui"
SENSOR = ROOT / ".ff_runs/20261116-025839-eval-mpu6050_drdy-full-2"
FAILED_ID = "20261116-140631-gui"
OUT = ROOT / "submission/screenshots"


def find_failed() -> Path:
    for p in (ROOT / ".ff_runs" / FAILED_ID, Path.home() / ".local/share/Trash/files" / FAILED_ID):
        if p.is_dir():
            return p
    raise SystemExit(f"找不到运行记录 {FAILED_ID}")


def main():
    app = QApplication([])
    QCoreApplication.setOrganizationName("FFsubmitshots")
    QSettings().clear()
    from firmforge.gui.app import MainWindow
    w = MainWindow(ROOT)
    w.apply_theme()
    w.show()
    w.resize(1600, 960)
    app.processEvents()

    def shot(name):
        for _ in range(4):
            app.processEvents()
        w.grab().save(str(OUT / f"{name}.png"))
        print("已保存", name)

    class Idle:                                # 假的后台：卡片上的按钮能显示，点了也不会真的跑
        def answer(self, v):
            pass

    c = w.chat
    w.go("chat")
    c.open_run(MAIN)
    shot("01_对话_一次完整运行")
    c.view_seg_emb.changed.emit("wiring")
    shot("02_接线图")
    c.view_seg_emb.changed.emit("flow")
    shot("03_流程图")
    c.view_seg_emb.changed.emit("map")
    c.select_module("imu")
    c.tabs.setCurrentWidget(c.timeline)
    shot("04_修正过程_诊断与交接核对")
    c.toggle_expand()
    shot("11_工程图展开")
    c.toggle_expand()
    c.select_module("imu")
    c.tabs.setCurrentWidget(c.why_box)
    shot("15_调度明细")

    # 接线清单卡片：用这次真跑第二次等接线时的真实内容（带着第一次自检没对上的结果）
    ev = [json.loads(line) for line in open(MAIN / "events.jsonl", encoding="utf-8")]
    wait = [e for e in ev if e["type"] == "human_wait" and e.get("node_id") == "wiring"][-1]["data"]
    c.new_chat()
    c.backend = Idle()
    c.add_user((MAIN / "spec.md").read_text(encoding="utf-8"))
    c.on_ask("wiring", {"connections": wait["connections"], "states": wait["states"], "failures": wait["failures"]})
    c.scroll.verticalScrollBar().setValue(0)
    shot("05_接线清单与自检没对上")

    # 没通过时的讲解卡片
    from firmforge.embedded.design import DesignModule
    from firmforge.embedded.explain import explain_failure, release_risks
    from firmforge.runs import read_events
    rd = find_failed()
    d = json.load(open(rd / "design.json", encoding="utf-8"))
    m = [x for x in d["modules"] if x["id"] == "adc_reader"][0]
    mod = DesignModule(m["id"], m["title"], m["layer"], tuple(m["files"]), tuple(m["provides"]), tuple(m["scopes"]), (),
                       tuple(m["acceptance"]))
    c.new_chat()
    c.backend = Idle()
    report = (rd / "nodes/adc_reader/attempt_4/report.txt").read_text(encoding="utf-8")
    c.on_ask("failed", {"module": "adc_reader", "attempts": 4, "explain": explain_failure(read_events(rd), rd, mod),
                        "risks": release_risks(mod, None, None, {"x": 1}, report)})
    c.scroll.verticalScrollBar().setValue(0)
    shot("06_没通过的讲解与人工放行")
    c.backend = None
    c.new_chat()

    for key, name in (("hw", "07_知识库"), ("flow", "08_工作流设计器"), ("team", "09_Agent团队"), ("set", "10_设置")):
        w.go(key)
        shot(name)

    w.go("chat")
    c.open_run(SENSOR)
    shot("12_工程图_带传感器")
    c.view_seg_emb.changed.emit("wiring")
    shot("13_接线图_带传感器")
    c.view_seg_emb.changed.emit("map")
    c.toggle_expand()
    shot("14_工程图展开_带传感器")
    c.toggle_expand()

    w.go("hw")
    k = w.hardware
    k.search.setText("WHO_AM_I")
    app.processEvents()
    for i in range(k.list.count()):
        it = k.list.item(i)
        if it.data(Qt.UserRole):
            k.list.setCurrentItem(it)
            break
    shot("16_知识库_规则出处")


if __name__ == "__main__":
    main()
