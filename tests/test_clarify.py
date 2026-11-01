"""需求澄清：解析需求文档与硬件信息、程序分配引脚（DECISIONS 第 66 条第 ① 步）。"""

import json
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.clarify import (Board, allocate, clarify_messages, confirm_brief, list_boards, parse_clarified,
                                        wiring_table)

HW = Path(helpers.ROOT) / "hardware"


def reply(signals, devices=None, board="esp32s3_supermini"):
    data = {"board": board, "devices": devices if devices is not None else
            [{"name": "imu", "part": "MPU6050", "bus": "i2c0", "address": "0x68"}], "signals": signals}
    return "<<<FILE spec.md\n# 姿态读取\n## 功能\n- 每 100 ms 读一次\n>>>FILE\n```json\n" + json.dumps(data) + "\n```"


class ClarifyTest(unittest.TestCase):
    def setUp(self):
        self.boards = list_boards(HW)
        self.board = self.boards["esp32s3_supermini"]

    def test_board_and_messages(self):
        self.assertIn(4, self.board.exposed)
        self.assertNotIn(19, self.board.exposed)
        msgs = clarify_messages([{"role": "user", "content": "我想读陀螺仪"}], self.boards)
        self.assertIn("esp32s3_supermini：ESP32-S3 SuperMini（芯片 esp32s3，esp-idf v6.1）", msgs[0]["content"])
        self.assertEqual(msgs[1]["content"], "我想读陀螺仪")

    def test_questions(self):
        c, problems = parse_clarified('```json\n{"questions": ["陀螺仪是什么型号？"]}\n```')
        self.assertEqual((c.questions, problems), (["陀螺仪是什么型号？"], []))

    def test_allocate_keeps_template_and_fills_free(self):
        c, problems = parse_clarified(reply(["i2c0.sda", "i2c0.scl", "imu.int", "buzzer"]))
        self.assertEqual(problems, [])
        self.assertEqual(c.devices[0].address, 0x68)
        prof, probs = allocate(self.board, c)
        self.assertEqual(probs, [])
        self.assertEqual(prof.pins["i2c0.sda"].value, 4)                   # 沿用模板（接线不用改）
        self.assertEqual(prof.pins["imu.int"].value, 7)
        buzzer = prof.pins["buzzer"]
        self.assertEqual(buzzer.origin, "default")
        self.assertNotIn(buzzer.value, prof.reserved_pins)
        self.assertIn(buzzer.value, self.board.exposed)
        self.assertNotIn(buzzer.value, {4, 5, 7})
        self.assertNotIn("uart1.tx", prof.pins)                           # 没要的信号不带过来
        self.assertIn("buzzer：GPIO", wiring_table(prof))
        self.assertIn("（自动分配）", wiring_table(prof))
        self.assertIn("接线表", confirm_brief(prof, c.spec).render())

    def test_out_of_pins_and_bad_input(self):
        many = [f"led{i}" for i in range(40)]
        _, probs = allocate(self.board, parse_clarified(reply(many))[0])
        self.assertTrue(any("没有空闲引脚" in p for p in probs))
        self.assertTrue(parse_clarified("```json\n{\"board\": \"x\"}\n```")[1])          # 缺需求文档
        self.assertTrue(parse_clarified(reply(["I2C0 SDA"]))[1])                         # 信号名不合格
        self.assertTrue(parse_clarified(reply(["led"], devices=[{"name": "x"}]))[1])     # 器件缺字段


if __name__ == "__main__":
    unittest.main()
