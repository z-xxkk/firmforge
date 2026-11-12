"""工程图和接线图（DECISIONS 第 109、113 条）：数据只来自能保证准确的来源；对话页上板流程时用开关切换工程图、接线图、流程图，
"展开"后积木里列出接口；选中模块时详情上方有接口概要；运行中有新结果时刷新。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.project_map import build_project_map, latest_files
from firmforge.grounding.profile import HardwareProfile
from firmforge.target.harness import BEGIN, END

C3 = HardwareProfile.load(Path(helpers.ROOT) / "hardware" / "esp32c3_supermini" / "profile.json")


def make_demo_run(rd: Path) -> Path:
    """一次多模块上板运行的记录：逻辑层 imu_logger 用到三个驱动；mpu6050 两个用例没过；两个器件都应答了。"""
    design = {"modules": [
        {"id": "mpu6050", "title": "MPU6050 驱动", "layer": "hardware", "scopes": ["i2c", "mpu6050"],
         "files": ["mpu6050.c", "include/mpu6050.h"], "depends_on": [],
         "provides": ["esp_err_t mpu_init(i2c_master_bus_handle_t bus)", "esp_err_t mpu_read_accel(float g[3])",
                      "esp_err_t mpu_read_temp(float *c)"]},
        {"id": "max30102_temp", "title": "MAX30102 片内温度", "layer": "hardware", "scopes": ["i2c", "max30102"],
         "files": ["max30102_temp.c", "include/max30102_temp.h"], "depends_on": [],
         "provides": ["esp_err_t ppg_temp_init(i2c_master_bus_handle_t bus)", "esp_err_t ppg_temp_read(float *c)"]},
        {"id": "uart_out", "title": "串口输出", "layer": "hardware", "scopes": ["uart"],
         "files": ["uart_out.c", "include/uart_out.h"], "depends_on": [],
         "provides": ["esp_err_t uart_out_init(void)", "int uart_out_write(const char *s)"]},
        {"id": "imu_logger", "title": "每秒读数并输出", "layer": "logic", "scopes": [],
         "files": ["imu_logger.c", "include/imu_logger.h"], "depends_on": ["mpu6050", "max30102_temp", "uart_out"],
         "provides": ["void logger_start(void)", "void logger_tick(void)"]}],
        "app": "每秒读一次", "wiring": {"i2c0.sda": 4, "i2c0.scl": 5, "uart1.tx": 0, "uart1.rx": 1}}
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "design.json").write_text(json.dumps(design, ensure_ascii=False), encoding="utf-8")
    (rd / "wiring.json").write_text(json.dumps({"source": "design", "pins": design["wiring"],
        "devices": [{"name": "imu", "part": "MPU6050", "bus": "i2c0", "address": 104},
                    {"name": "ppg", "part": "MAX30102", "bus": "i2c0", "address": 87}],
        "checked": [{"device": "imu", "part": "MPU6050", "ok": True, "pins": "i2c0.sda=GPIO4、i2c0.scl=GPIO5", "why": "在 0x68 应答了"},
                    {"device": "ppg", "part": "MAX30102", "ok": True, "pins": "i2c0.sda=GPIO4、i2c0.scl=GPIO5", "why": "在 0x57 应答了"}]},
        ensure_ascii=False), encoding="utf-8")
    code = {
        "mpu6050": ('#include "hw_config.h"\n#include "mpu6050.h"\nesp_err_t mpu_init(i2c_master_bus_handle_t b){ return add(b, FF_IMU_ADDR); }\n'
                    'esp_err_t mpu_read_accel(float g[3]){ /* mpu_read_temp 不算 */ return 0; }\nesp_err_t mpu_read_temp(float *c){ return 0; }\n',
                    'TEST_CASE("读到 WHO_AM_I", "[m]") { mpu_init(bus); }\nTEST_CASE("静止时约 1 g", "[m]") { float g[3]; mpu_read_accel(g); }\n'
                    'TEST_CASE("量程切换后读数", "[m]") { float g[3]; mpu_init(bus); mpu_read_accel(g); }\n',
                    "main/t.c:1:读到 WHO_AM_I:PASS\nmain/t.c:2:静止时约 1 g:FAIL:Expected 1.0 Was 4.0\nmain/t.c:3:量程切换后读数:FAIL:x\n"),
        "max30102_temp": ('#include "hw_config.h"\nesp_err_t ppg_temp_init(i2c_master_bus_handle_t b){ return add(b, FF_PPG_ADDR); }\nesp_err_t ppg_temp_read(float *c){ return 0; }\n',
                          'TEST_CASE("温度合理", "[t]") { float c; ppg_temp_read(&c); }\n', "main/t.c:1:温度合理:PASS\n"),
        "uart_out": ('#include "hw_config.h"\nesp_err_t uart_out_init(void){ return pins(FF_PIN_UART1_TX, FF_PIN_UART1_RX); }\nint uart_out_write(const char *s){ return 0; }\n',
                     'TEST_CASE("回环收到", "[u]") { uart_out_init(); uart_out_write("x"); }\n', "main/t.c:1:回环收到:PASS\n"),
        "imu_logger": ('#include "mpu6050.h"\nvoid logger_start(void){ mpu_init(0); ppg_temp_init(0); uart_out_init(); }\n'
                       'void logger_tick(void){ float g[3]; mpu_read_accel(g); float c; ppg_temp_read(&c); uart_out_write("x"); }\n',
                       'static void test_tick(void){ logger_tick(); }\nint main(void){ RUN_TEST(test_tick); }\n', None),
    }
    for mid, (c, t, log) in code.items():
        a = rd / "nodes" / mid / "attempt_1"
        layer = next(m["layer"] for m in design["modules"] if m["id"] == mid)
        (a / "main" / ("test" if layer == "hardware" else "test_host")).mkdir(parents=True, exist_ok=True)
        (a / "main" / f"{mid}.c").write_text(c, encoding="utf-8")
        (a / "main" / ("test" if layer == "hardware" else "test_host") / f"test_{mid}.c").write_text(t, encoding="utf-8")
        if log is not None:
            n = log.count("\n")
            fails = log.count(":FAIL")
            (a / "serial.log").write_text(f"{BEGIN}\n{log}{n} Tests {fails} Failures 0 Ignored\n{'OK' if not fails else 'FAIL'}\n{END}\n", encoding="utf-8")
            (a / "report.txt").write_text("上板", encoding="utf-8")
        else:
            (a / "report.txt").write_text("通过 4 个，失败 0 个", encoding="utf-8")
    ev = [{"type": "run_started", "node_id": None, "data": {"config": "embedded/verified", "board": "esp32c3_supermini"}},
          {"type": "plan_ready", "node_id": "design", "data": {"modules": design["modules"]}}]
    for m in design["modules"]:
        ev.append({"type": "node_ready", "node_id": m["id"], "data": {"title": m["title"], "layer": m["layer"]}})
        ok = m["id"] != "mpu6050"
        ev.append({"type": "node_done" if ok else "node_failed", "node_id": m["id"], "data": {"verdict": "pass" if ok else "fail", "attempts": 1}})
    ev.append({"type": "run_finished", "node_id": None, "data": {"verdict": "fail"}})
    (rd / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev), encoding="utf-8")
    return rd



class ProjectMapDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = make_demo_run(Path(self.tmp.name) / "run")
        self.pm = build_project_map(self.rd, C3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_modules_results_and_failing_uses(self):
        by = self.pm.by_id
        self.assertEqual([m.id for m in self.pm.modules], ["mpu6050", "max30102_temp", "uart_out", "imu_logger"])
        m = by["mpu6050"]
        self.assertEqual((m.status, m.result), ("fail", "上板 1/3"))
        self.assertEqual(m.failed_cases, ["静止时约 1 g", "量程切换后读数"])
        self.assertEqual(m.failing_uses, ["mpu_init", "mpu_read_accel"])        # 两个没过的用例里字面用到的接口
        self.assertEqual((by["imu_logger"].status, by["imu_logger"].result), ("pass", "电脑上测 4/4"))
        self.assertEqual(m.used_by, ["imu_logger"])
        self.assertEqual(self.pm.summary(), {"modules": 4, "passed": 3, "tested": 4, "pins": 4, "devices": 2})

    def test_edges_are_literal_uses_only(self):
        e = {x.dst: x.uses for x in self.pm.edges}
        self.assertEqual(e["mpu6050"], ["mpu_init", "mpu_read_accel"])          # mpu_read_temp 只在注释里，不算
        self.assertEqual(e["uart_out"], ["uart_out_init", "uart_out_write"])

    def test_pins_devices_and_wiring(self):
        by = self.pm.by_id
        self.assertEqual(by["uart_out"].signals, ["uart1.rx", "uart1.tx"])      # 来自 FF_PIN_* 宏
        self.assertEqual(by["mpu6050"].devices, ["imu"])                       # 来自 FF_IMU_ADDR
        self.assertEqual(self.pm.module_links("mpu6050"), ([], ["imu"]))
        self.assertEqual(sorted(p.id for p in self.pm.peripherals), ["i2c0", "uart1"])
        self.assertEqual([(d.name, d.checked) for d in self.pm.devices], [("imu", True), ("ppg", True)])
        self.assertEqual(self.pm.wiring_source, "design")
        self.assertEqual(self.pm.unused_gpios, [3, 6, 7, 10, 11])               # C3 的 0–21 去掉保留和用掉的
        self.assertNotIn(8, self.pm.unused_gpios)

    def test_without_design_or_wiring(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(build_project_map(Path(d), C3))                  # 还没设计
            rd = make_demo_run(Path(d) / "r")
            (rd / "wiring.json").unlink()                                       # 旧的运行记录：没有接线文件就用档案的默认接线
            pm = build_project_map(rd, C3)
            self.assertEqual(pm.wiring_source, "default")
            self.assertEqual(pm.pins["i2c0.sda"], 4)
            self.assertTrue(all(d.checked is None for d in pm.devices))         # 没有实测记录：不说确认过
            self.assertEqual(latest_files(rd, "nope"), {})

    def test_skipped_and_compile_failures(self):
        a = self.rd / "nodes" / "uart_out" / "attempt_2"
        (a / "main").mkdir(parents=True)
        (a / "main" / "uart_out.c").write_text("int x;", encoding="utf-8")
        (a / "report.txt").write_text("编译失败：\nmain/uart_out.c:1: error: x", encoding="utf-8")
        pm = build_project_map(self.rd, C3)
        self.assertEqual((pm.by_id["uart_out"].status, pm.by_id["uart_out"].result, pm.by_id["uart_out"].attempt),
                         ("fail", "编译没过", 2))
        (a / "report.txt").write_text("没有配置板子，硬件层只做到编译和合规检查", encoding="utf-8")
        self.assertEqual(build_project_map(self.rd, C3).by_id["uart_out"].status, "skipped")    # 没上板：不说通过


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class ProjectMapGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeMapTest")
        set_theme(th.current(False))

    def setUp(self):
        self.errors = []
        self.saved_hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.errors.append(f"{t.__name__}: {v}")
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = make_demo_run(Path(self.tmp.name) / "run")

    def tearDown(self):
        sys.excepthook = self.saved_hook
        self.tmp.cleanup()

    def page(self):
        from firmforge.gui.chat_page import ChatPage
        p = ChatPage(Path(helpers.ROOT))
        p.flow_combo.setCurrentIndex(p.flow_combo.findData("embedded"))
        return p

    def test_switch_expand_and_summary(self):
        p = self.page()
        self.assertTrue(p.view_seg_emb.isVisibleTo(p))
        self.assertFalse(p.view_seg.isVisibleTo(p))
        p.open_run(self.rd)
        self.assertIs(p.graph_stack.currentWidget(), p.pmap)
        self.assertEqual(set(p.pmap.cards), {"mpu6050", "max30102_temp", "uart_out", "imu_logger"})
        self.assertEqual(p.pmap.edge_labels, [])                                # 紧凑：连线上不写函数名
        self.assertIn("mpu_read_accel", p.pmap.cards["mpu6050"].toolTip())      # 鼠标停上去能看到接口
        self.assertIn("测试过 3/4", p.graph_sub.text())
        p.pmap.module_clicked.emit("mpu6050")
        self.assertEqual(p.graph.selected, "mpu6050")
        self.assertTrue(p.iface_label.isVisibleTo(p))
        self.assertIn("没过的用例用到", p.iface_label.text())
        self.assertIn("imu_logger", p.iface_label.text())
        p.toggle_expand()
        self.assertTrue(p.pmap.expanded)
        self.assertEqual(p.split.sizes()[0], 0)                                 # 会话列表收起
        self.assertTrue(p.pmap.edge_labels)                                     # 展开：连线上写出用到的函数
        self.assertGreater(p.pmap.cards["mpu6050"].h, 52)                       # 积木里列出接口
        p.toggle_expand()
        self.assertFalse(p.pmap.expanded)
        p.view_seg_emb.changed.emit("wiring")
        self.assertIs(p.graph_stack.currentWidget(), p.wiring_view)
        self.assertGreater(len(p.wiring_view.scene().items()), 10)
        p.flow_combo.setCurrentIndex(p.flow_combo.findData("classic"))         # 纯逻辑流程：还是模块图
        self.assertTrue(p.view_seg.isVisibleTo(p))
        self.assertIs(p.graph_stack.currentWidget(), p.graph)
        self.assertEqual(self.errors, [])

    def test_live_refresh_and_empty_state(self):
        p = self.page()
        p.run_dir = self.rd
        p._refresh_map()
        self.assertEqual(len(p.pmap.cards), 4)
        p.clear_graph()
        self.assertEqual(p.pmap.cards, {})                                      # 新的运行：先清空
        p.run_dir = self.rd
        p.on_event({"type": "run_started", "node_id": None, "data": {"config": "embedded/verified"}})
        p.on_event({"type": "node_ready", "node_id": "mpu6050", "data": {"title": "x", "layer": "hardware"}})
        self.assertTrue(p._map_timer.isActive())                                # 合并成一次重画
        p._map_timer.stop()
        p._refresh_map()
        self.assertEqual(p.pmap.cards["mpu6050"].status, "running")             # 运行中：进行中
        p.on_event({"type": "node_failed", "node_id": "mpu6050", "data": {"verdict": "fail"}})
        p._map_timer.stop()
        p._refresh_map()
        self.assertEqual(p.pmap.cards["mpu6050"].status, "fail")                # 结束：按最后一次检查的结果
        from firmforge.gui.project_map_view import ProjectMapView, WiringView
        v, w = ProjectMapView(), WiringView()
        v.set_map(None)
        w.set_map(None)
        self.assertEqual(len(v.scene().items()), 1)                             # 只有一行说明文字
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
