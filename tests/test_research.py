"""查资料 Agent 的工具与消息、规则解析、语义核对 Agent（DECISIONS 第 66 条第 ②、③ 步）。"""

import unittest

import helpers  # noqa: F401
from firmforge.grounding.research import (PageIndex, SOURCE_TOOLS, library_listing, llm_semantic_checker,
                                          parse_rules, parse_verdict, research_messages)
from firmforge.grounding.rules import RuleSet
from firmforge.grounding.verifier import RuleResult
from firmforge.llm import FakeLLM
from firmforge.tools.registry import ToolContext
from test_rule_verifier import good_rules, make_library, make_profile

TOOLS = {t.name: t for t in SOURCE_TOOLS}


class PageIndexTest(unittest.TestCase):
    def test_search_and_tools(self):
        lib = make_library()
        idx = PageIndex(lib)
        hits = idx.search("sleep mode power-up")
        self.assertEqual(hits[0][:2], ("mpu6050-ps", 2))
        self.assertIn("sleep mode", hits[0][2])
        self.assertEqual(idx.search("i2c_new_master_bus")[0][:2], ("idf-i2c-master-h", None))
        self.assertTrue(all(d == "esp32s3-ds" for d, _, _ in idx.search("I2C", doc="esp32s3-ds")))

        ctx = ToolContext(library=lib)
        out = TOOLS["doc_search"].execute({"query": "slave address AD0"}, ctx)
        self.assertIn("mpu6050-ps 第 1 页", out)
        self.assertIsNotNone(ctx.page_index)                                   # 第一次查时建立索引
        page = TOOLS["doc_page"].execute({"doc": "esp32s3-ds", "page": 2}, ctx)
        self.assertIn("GPIO19 and GPIO20 are used by USB", page)
        self.assertIn("第 2 页", page)
        self.assertIn("i2c_new_master_bus", TOOLS["doc_page"].execute({"doc": "idf-i2c-master-h"}, ctx))
        with self.assertRaises(ValueError):
            TOOLS["doc_search"].execute({"query": " "}, ctx)
        with self.assertRaises(ValueError):
            TOOLS["doc_search"].execute({"query": "x"}, ToolContext())
        self.assertIn("没有找到", TOOLS["doc_search"].execute({"query": "zzzqqq"}, ctx))

    def test_doc_page_shows_layout_text(self):
        # PDF 有按版面的文字时，doc_page 两段都给：逐字文本 + 保留列对齐的版面文字（DECISIONS 第 98 条）
        from firmforge.grounding.sources import Document, SourceLibrary
        lib = SourceLibrary(make_library().root)
        lib.add(Document("max", "MAX30102", "pdf", "official_manual", pages=("Status 2 DIE_ TEMP_RDY 0x01",),
                         layout=("B7  B6  B1  B0\n        DIE_\n      TEMP_RDY",)))
        out = TOOLS["doc_page"].execute({"doc": "max", "page": 1}, ToolContext(library=lib))
        self.assertIn("【逐字文本】", out)
        self.assertIn("【按版面排列】", out)
        self.assertLess(out.index("Status 2 DIE_"), out.index("B7  B6  B1  B0"))
        plain = TOOLS["doc_page"].execute({"doc": "esp32s3-ds", "page": 2}, ToolContext(library=make_library()))
        self.assertNotIn("按版面", plain)                                       # 没有版面文字就照旧


class ResearchMessagesTest(unittest.TestCase):
    def test_first_round_and_feedback(self):
        lib, prof = make_library(), make_profile(**{"i2c0.sda": 8})
        msgs = research_messages(prof, ["i2c", "mpu6050"], lib)
        system, user = msgs[0]["content"], msgs[1]["content"]
        self.assertIn("逐字摘抄", system)
        self.assertIn("api（接口用法）", system)                          # 规则类型清单
        self.assertIn("i2c、mpu6050", user)
        self.assertIn("esp-idf v6.1", user)
        self.assertIn("i2c0.sda=GPIO8", user)
        self.assertIn("mpu6050-ps：MPU-6000/6050 Product Specification（pdf，official_manual，2 页）", user)
        trusted = RuleSet([r.advance("checked").advance("approved").advance("trusted") for r in list(good_rules())[:1]])
        bad = RuleResult(list(good_rules())[1], steps=[(2, "wrong", "esp32s3-ds 第 2 页找不到引用的原文")])
        again = research_messages(prof, ["i2c"], lib, trusted=trusted, feedback=[bad],
                                  missing=["mpu6050 缺少这些类别的规则：操作顺序"])[1]["content"]
        self.assertIn("修正上一轮被退回的规则", again)
        self.assertIn("已有的可信规则", again)
        self.assertIn("第 2 步：esp32s3-ds 第 2 页找不到引用的原文", again)
        self.assertIn("操作顺序", again)
        self.assertIn("（没有登记任何资料）", library_listing(type(lib)(lib.root)))


class ParseTest(unittest.TestCase):
    def test_parse_rules(self):
        text = """好的，规则如下：
```json
[{"id": "R-I2C-001", "kind": "api", "scope": ["i2c"], "text": "用 i2c_new_master_bus()",
  "cite": {"doc": "idf-i2c-master-h", "page": null, "section": "", "quote": "i2c_new_master_bus("},
  "tier": "official_code", "status": "trusted"},
 {"id": "R-I2C-002"},
 "随便一句话"]
```"""
        rules, errors = parse_rules(text)
        self.assertEqual([r.id for r in rules], ["R-I2C-001"])
        self.assertEqual(rules[0].status, "draft")                     # 模型自称可信也没用
        self.assertEqual(len(errors), 2)
        self.assertEqual(parse_rules('{"rules": []}'), ([], []))
        self.assertTrue(parse_rules("没有 JSON")[1])
        self.assertTrue(parse_rules('{"a": 1}')[1])

    def test_semantic_checker(self):
        llm = FakeLLM(['```json\n{"faithful": false, "reason": "原文说上电处于睡眠模式"}\n```'])
        check = llm_semantic_checker(llm, "fake")
        rule = list(good_rules())[-1]
        ok, why = check(rule, "The device will come up in sleep mode upon power-up.")
        self.assertFalse(ok)
        self.assertIn("睡眠", why)
        sent = llm.all_text_sent()
        self.assertIn("只判断一件事", sent)
        self.assertIn("come up in sleep mode", sent)
        self.assertEqual(llm.calls[0]["temperature"], 0.0)
        self.assertEqual(parse_verdict("不是 JSON"), (False, "核对 Agent 的回复格式不对，按不通过处理"))
        self.assertEqual(parse_verdict('{"faithful": true}'), (True, "（没有给理由）"))


if __name__ == "__main__":
    unittest.main()
