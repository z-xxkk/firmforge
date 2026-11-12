"""工作流设计器的"猴子 + 不变量"测试（Android Monkey 的随机操作 + Hypothesis 状态机测试的不变量检查）。

用固定的随机种子做一长串随机操作：从步骤箱拖入、拖动（小范围 / 吸附到连线、步骤下面、分支旁边、第一步上方 / 空白处）、
从小圆点拉线、点连线上的 ×、删除、撤销、改名、把代码写回去。拖动和拉线用真实的鼠标事件（QTest）。
每一步之后检查不变量；出问题时报告随机种子和第几步，用同一个种子能原样重现。

单元测试（test_gui_monkey.py）跑几个种子各几百步；要跑得更久：
    QT_QPA_PLATFORM=offscreen python tests/monkey_designer.py --seeds 0-19 --steps 400
"""

from __future__ import annotations

import dataclasses
import random
import tomllib
import traceback

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest

KINDS = ["agent", "verify", "human", "parallel", "condition", "loop", "subflow", "foreach"]


class Monkey:
    def __init__(self, window, seed: int, log=None):
        from firmforge.gui.graph import STEP_H, STEP_MIME, STEP_W
        self.w, self.d = window, window.designer
        self.cv = self.d.canvas
        self.vp = self.cv.viewport()
        self.rng = random.Random(seed)
        self.seed = seed
        self.W, self.H, self.MIME = STEP_W, STEP_H, STEP_MIME
        self.log = log or (lambda s: None)
        self.history: list[str] = []
        self._popups: list[str] = []
        import firmforge.gui.designer_page as dp
        from PySide6.QtWidgets import QMessageBox
        self._restore = [(QMessageBox, "warning", QMessageBox.__dict__.get("warning", QMessageBox.warning)),
                         (QMessageBox, "information", QMessageBox.__dict__.get("information", QMessageBox.information)),
                         (dp, "save_workflow", dp.save_workflow)]
        QMessageBox.warning = staticmethod(lambda *a, **k: self._popups.append(str(a[2])[:60]))
        QMessageBox.information = staticmethod(lambda *a, **k: self._popups.append(str(a[2])[:60]))
        dp.save_workflow = lambda wf, *a, **k: None          # 不写真文件
        self.w.go("flow")

    def restore(self):
        """把换掉的弹窗和保存函数换回来（不然会影响后面跑的其他测试）。"""
        for obj, name, value in reversed(self._restore):
            setattr(obj, name, value)

    # ------------------------------------------------------------ 基本动作
    def _process(self):
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _vp(self, scene_pt: QPointF) -> QPoint:
        return self.cv.mapFromScene(scene_pt)

    def _drag(self, a: QPoint, b: QPoint):
        QTest.mousePress(self.vp, Qt.LeftButton, Qt.NoModifier, a)
        for k in range(1, 7):
            QTest.mouseMove(self.vp, a + (b - a) * k / 6)
        QTest.mouseRelease(self.vp, Qt.LeftButton, Qt.NoModifier, b)
        self._process()

    def _items(self):
        return list(self.cv.items_by_id.values())

    def _pick(self, pred=lambda it: True):
        its = [it for it in self._items() if pred(it)]
        return self.rng.choice(its) if its else None

    def _target_point(self) -> tuple[str, QPointF]:
        """随机挑一个放下去的地方（场景坐标，是被拖步骤上沿中点该到的位置）。"""
        r, W, H = self.rng.random(), self.W, self.H
        edges = [e for e in self.cv.edges if not e.branch and e.isVisible()]
        if r < 0.25 and edges:
            e = self.rng.choice(edges)
            return f"连线 {e.logical[0]}→{e.logical[1]}", e.mid()
        it = self._pick()
        if it is None:
            return "空白", QPointF(self.rng.uniform(-800, 1400), self.rng.uniform(-300, 900))
        p = it.pos()
        if r < 0.5:
            return f"{it.step.id} 下面", p + QPointF(W / 2 + self.rng.uniform(-30, 30), H + self.rng.uniform(5, 60))
        if r < 0.65:
            side = self.rng.choice([-1, 1])
            return f"{it.step.id} {'左' if side < 0 else '右'}边", p + QPointF(W / 2 + side * (W + self.rng.uniform(10, 60)), 0)
        if r < 0.75:
            return f"{it.step.id} 上方", p + QPointF(W / 2, -H - self.rng.uniform(5, 40))
        return "空白", QPointF(self.rng.uniform(-800, 1400), self.rng.uniform(-300, 900))

    # ------------------------------------------------------------ 随机操作
    def op_drop_new(self):
        d = self.w.designer if hasattr(self, "w") else None
        pal = [d.palette.item(i).data(0x0100) for i in range(d.palette.count())] if d is not None else []
        kind = self.rng.choice([k for k in pal if k] or KINDS)          # 照当前工作流的步骤箱拖（上板流程的步骤箱不一样）
        where, pt = self._target_point()
        md = QMimeData()
        md.setData(self.MIME, kind.encode())
        self.cv.dropEvent(QDropEvent(QPointF(self._vp(pt)), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        self._process()
        return f"从步骤箱拖 {kind} 到 {where}"

    def op_drag(self):
        it = self._pick()
        if it is None:
            return None
        grab = QPointF(30, 12) if it.step.kind != "parallel" else QPointF(self.W / 2, 12)
        a = self._vp(it.pos() + grab)
        if self.rng.random() < 0.25:
            b = a + QPoint(self.rng.randint(-30, 30), self.rng.randint(-20, 20))
            where = "小范围挪动"
        else:
            where, pt = self._target_point()
            b = self._vp(pt - QPointF(self.W / 2, 0) + grab)
        sid = it.step.id
        self._drag(a, b)
        return f"拖 {sid} 到 {where}"

    def op_line(self):
        a, b = self._pick(), self._pick()
        if a is None or b is None:
            return None
        sa, sb = a.step.id, b.step.id
        self._drag(self._vp(a.pos() + QPointF(self.W / 2, self.H + 6)), self._vp(b.pos() + QPointF(self.W / 2, self.H / 2)))
        return f"从 {sa} 拉线到 {sb}"

    def op_cut(self):
        edges = [e for e in self.cv.edges if e.deletable and e.isVisible()]
        if not edges:
            return None
        e = self.rng.choice(edges)
        p = self._vp(e.mid())
        QTest.mouseMove(self.vp, p)
        QTest.mouseClick(self.vp, Qt.LeftButton, Qt.NoModifier, p)
        self._process()
        return f"点 {e.a}→{e.b} 上的 ×"

    def op_delete(self):
        it = self._pick()
        if it is None:
            return None
        sid = it.step.id
        QTest.mouseClick(self.vp, Qt.LeftButton, Qt.NoModifier, self._vp(it.pos() + QPointF(30, 12)))
        self.cv.setFocus()
        QTest.keyClick(self.cv, Qt.Key_Delete)
        self._process()
        return f"选中 {sid} 按 Delete"

    def op_undo(self):
        self.cv.setFocus()
        QTest.keyClick(self.cv, Qt.Key_Z, Qt.ControlModifier)
        self._process()
        return "Ctrl+Z"

    def op_rename(self):
        it = self._pick()
        if it is None:
            return None
        sid = it.step.id
        self.d.select(sid)
        new = sid + self.rng.choice(["_a", "_b", "x"])
        self.d._set_field("id", new)
        return f"把 {sid} 改名为 {new}"

    def op_code(self):
        self.d.code.setPlainText(self.d.code.toPlainText())
        self.d._apply_code()
        return "代码原样写回"

    OPS = [("op_drop_new", 3), ("op_drag", 6), ("op_line", 3), ("op_cut", 2), ("op_delete", 1), ("op_undo", 2),
           ("op_rename", 1), ("op_code", 1)]

    # ------------------------------------------------------------ 不变量
    def problems(self, before) -> list[str]:
        from firmforge.gui.layout import attached_ids
        from firmforge.orchestrator.workflow import dump_workflow, parse_workflow
        d, cv, W, H = self.d, self.cv, self.W, self.H
        out: list[str] = []
        flows = {"main": list(d.wf.main), **{n: list(s) for n, s in d.wf.subflows.items()}}
        shown = ["main", d.current_sub] if d.current_sub else ["main"]
        # ① 画面上的步骤和数据一致
        ids = {s.id for f in shown for s in flows.get(f, [])}
        if set(cv.items_by_id) != ids:
            out.append(f"画面上的步骤和数据不一致：多了 {set(cv.items_by_id) - ids}，少了 {ids - set(cv.items_by_id)}")
        # ② 步骤不叠在一起
        its = self._items()
        for i, a in enumerate(its):
            for b in its[i + 1:]:
                if abs(a.pos().x() - b.pos().x()) < W - 10 and abs(a.pos().y() - b.pos().y()) < H - 6:
                    out.append(f"步骤叠在一起：{a.step.id} / {b.step.id}")
        for name, steps in flows.items():
            sid = {s.id for s in steps}
            refs = [r for s in steps for r in (*s.branches, *s.then, *s.otherwise)]
            # ③ 引用都在同一个流程里、不重复、不引用自己
            for s in steps:
                for r in (*s.branches, *s.then, *s.otherwise):
                    if r not in sid:
                        out.append(f"{name}：{s.id} 引用了不存在的步骤 {r}")
                    if r == s.id:
                        out.append(f"{name}：{s.id} 引用了自己")
            dup = {r for r in refs if refs.count(r) > 1}
            if dup:
                out.append(f"{name}：步骤被引用了不止一次 {dup}")
            # ④ "连上了却显示没连上"：被引用的步骤不能标着没连上
            for s in steps:
                if s.detached and s.id in refs:
                    out.append(f"{name}：{s.id} 被引用了却标着没连上")
            # ⑤ 连着的步骤不记手动位置；没连上的步骤不算连着
            att = attached_ids(steps)
            for s in steps:
                if s.id in att and s.pos is not None and not s.detached:
                    out.append(f"{name}：连着的步骤 {s.id} 记了手动位置 {s.pos}")
            # ⑥ 名字不重复
            if len(sid) != len(steps):
                out.append(f"{name}：步骤名字重复")
        allids = [s.id for f in flows.values() for s in f]
        if len(set(allids)) != len(allids):
            out.append("不同流程里的步骤名字重复")
        # ⑦ 存成文件再读回来不变
        try:
            back = parse_workflow(tomllib.loads(dump_workflow(d.wf)), "monkey")
            if back != d.wf:
                diff = [s.id for f in ("main", *d.wf.subflows) for s, t in
                        zip((d.wf.main if f == "main" else d.wf.subflows[f]),
                            (back.main if f == "main" else back.subflows.get(f, ()))) if s != t]
                out.append(f"存成文件再读回来变了：{diff[:3]}")
        except Exception as exc:                            # noqa: BLE001
            out.append(f"存成文件再读回来出错：{exc}")
        # ⑧ 检查栏和数据一致：有没连上的步骤 ⇔ 检查里有"还没连上"
        has_det = any(s.detached for f in flows.values() for s in f)
        says = any("还没连上" in p for p in d.check(quiet=True))
        if has_det != says:
            out.append(f"检查栏和数据对不上：有没连上的步骤={has_det}，检查说没连上={says}")
        # ⑨ 主干连线：每个连着的、顶层的步骤（除了第一步）都有一条进来的线
        for name in shown:
            steps = flows.get(name, [])
            refd = {r for s in steps for r in (*s.branches, *s.then, *s.otherwise)}
            top = [s.id for s in steps if s.id not in refd and not s.detached]
            into = {e.logical[1] for e in cv.edges if not e.branch}
            for sid in top[1:]:
                if sid not in into:
                    out.append(f"{name}：{sid} 在主干上却没有连线连进来")
        return out

    # ------------------------------------------------------------ 跑
    def run(self, steps: int) -> list[str]:
        names = [n for n, k in self.OPS for _ in range(k)]
        changes: list = []                                  # 每次改动之前的状态，撤销要精确回到它
        import sys
        caught: list[str] = []                              # Qt 回调里抛的异常不会冒到这里，要用 excepthook 接住
        hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: caught.append("".join(traceback.format_exception(t, v, tb))[-700:])
        try:
            return self._run(steps, names, changes, caught)
        finally:
            sys.excepthook = hook
            self.restore()

    def _run(self, steps, names, changes, caught):
        for i in range(steps):
            before = self.d.wf
            name = self.rng.choice(names)
            try:
                what = getattr(self, name)()
            except Exception:                               # noqa: BLE001
                return [f"种子 {self.seed} 第 {i + 1} 步 {name} 出异常：\n" + traceback.format_exc()[-800:]] 
            if what is None:
                continue
            self.history.append(what)
            self._popups.clear()
            bad = self.problems(before)
            if caught:
                bad.insert(0, "没接住的异常：\n" + caught.pop(0))
            if name == "op_undo":
                if changes:
                    want = changes.pop()
                    if self.d.wf != want:                   # ⑩ 撤销精确回到上一次改动之前
                        bad.append("撤销没有回到上一次改动之前的状态")
                elif self.d.wf != before:
                    bad.append("没有可撤销的改动时按撤销，流程却变了")
            elif self.d.wf != before and name != "op_code":
                changes.append(before)
            if bad:
                recent = "\n    ".join(self.history[-6:])
                return [f"种子 {self.seed} 第 {i + 1} 步（{what}）之后：" + "；".join(bad[:4]) + f"\n  最近几步：\n    {recent}"]
        return []


def reset(window, name: str = "module_dev"):
    from firmforge.orchestrator.workflow import load_workflow
    d = window.designer
    d.wf = load_workflow(name)
    d.saved = d.wf
    d.undo.clear()
    d.new_ids.clear()
    d.selected = None
    d.current_sub = next(iter(d.wf.subflows), None)
    d.canvas.user_view = False
    d._fill_palette()
    d.render_all()


def main(argv=None):
    import argparse
    import os
    import sys
    from pathlib import Path
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--workflow", default="module_dev", help="在哪个工作流上随机操作：module_dev 或 firmware_dev")
    a = ap.parse_args(argv)
    lo, _, hi = a.seeds.partition("-")
    seeds = range(int(lo), int(hi or lo) + 1)
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("FirmForgeMonkey")
    QSettings().clear()
    from firmforge.gui.app import MainWindow
    w = MainWindow(Path(__file__).resolve().parents[1])
    w.apply_theme()
    w.resize(1440, 900)
    w.show()
    bad = 0
    for s in seeds:
        reset(w, a.workflow)
        m = Monkey(w, s)
        found = m.run(a.steps)
        print(f"种子 {s}：{'没发现问题' if not found else '发现问题'}（做了 {len(m.history)} 步）")
        for f in found:
            bad += 1
            print("  " + f)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
