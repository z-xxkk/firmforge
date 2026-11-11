"""对话、硬件、评测、团队、设置几页的随机点击测试（Android Monkey 的做法：固定种子的随机操作，出问题能原样重现）。

为了不产生副作用，猴子在一份临时的项目副本里跑：
- 模型换成假的（不联网、不花钱），回复内容随机：正常的、格式乱的、空的；
- ESP-IDF 和板子当作"找不到"（不编译、不烧录）；
- 弹窗、选文件夹、打开文件管理器都换成自动应答；
- 读写的配置、规则库、运行记录都在副本里，真实项目一律不碰。
不变量：不出任何异常（包括 Qt 槽函数里的异常）、面板不超出窗口、每一页都还能切过去。

单元测试（test_gui_monkey.py）跑几个种子；要跑得更久：
    QT_QPA_PLATFORM=offscreen python tests/monkey_pages.py --seeds 0-9 --steps 300
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ["firmforge.toml", "workflows", "agents", "tasks", "tasks_board", "hardware", "prompts", "kb", "third_party",
        ".env.example", "docs/eval_data"]


def make_sandbox() -> Path:
    """项目副本：带上评测题、硬件目录、几次真实运行记录（用来回放）和资料缓存（规则详情里的位标注）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ff-monkey-"))
    for rel in COPY:
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, tmp / rel, dirs_exist_ok=True)
        elif src.is_file():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
    lib = ROOT / ".ff_sources" / "library"
    if lib.is_dir():
        shutil.copytree(lib, tmp / ".ff_sources" / "library")
    runs = sorted((ROOT / ".ff_runs").glob("*-eval-*"))[-4:] if (ROOT / ".ff_runs").is_dir() else []
    for r in runs:
        shutil.copytree(r, tmp / ".ff_runs" / r.name)
    data = ROOT / "docs" / "eval_data" / "board_full_20261108"
    if data.is_dir():
        shutil.copytree(data, tmp / "results" / "board_full")
    return tmp


class Errors:
    """收集所有没接住的异常（Qt 槽函数里抛的异常 PySide6 交给 sys.excepthook）。"""

    def __init__(self):
        self.items: list[str] = []
        sys.excepthook = lambda t, v, tb: self.items.append("".join(traceback.format_exception(t, v, tb))[-900:])


_SAVED: list = []


def restore_world() -> None:
    """把 fake_world 换掉的东西全部换回来（不然会影响后面跑的其他测试）。"""
    while _SAVED:
        obj, name, value = _SAVED.pop()
        setattr(obj, name, value)


def _swap(obj, name, value) -> None:
    if not any(o is obj and n == name for o, n, _ in _SAVED):
        _SAVED.append((obj, name, obj.__dict__[name] if name in getattr(obj, "__dict__", {}) else getattr(obj, name)))
    setattr(obj, name, value)


def fake_world(rng: random.Random):
    """把会联网、会花钱、会弹窗、会动板子的地方换掉；用完调 restore_world() 换回来。"""
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
    from firmforge import llm as llm_mod
    from firmforge.llm import FakeLLM
    from firmforge.target import board as board_mod
    from firmforge.target import esp_idf

    def reply(messages):
        r = rng.random()
        if r < 0.3:
            return ""
        if r < 0.6:
            return "随便说点什么，不按任何格式。"
        return "```json\n{\"faithful\": true, \"reason\": \"一致\"}\n```"
    class _FakeClient(FakeLLM):                    # 设置页"测试连接并列出型号"要用 list_models
        def list_models(self):
            return ["deepseek-fake"]
    _swap(llm_mod.OpenAICompatClient, "from_settings", staticmethod(lambda s, **k: _FakeClient(reply)))

    def no_idf(*a, **k):
        raise esp_idf.IdfUnavailable("猴子测试：当作没装 ESP-IDF")
    _swap(esp_idf.IdfEnv, "load", staticmethod(no_idf))
    _swap(board_mod, "find_port", lambda *a, **k: None)
    for name, value in (("warning", QMessageBox.Ok), ("information", QMessageBox.Ok), ("critical", QMessageBox.Ok),
                        ("question", QMessageBox.No)):
        _swap(QMessageBox, name, staticmethod(lambda *a, _v=value, **k: _v))
    _swap(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: tempfile.mkdtemp(prefix="ff-export-")))
    _swap(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    _swap(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    _swap(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    _swap(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    _swap(QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: (rng.choice(["审查之后加一步人工验收", ""]), True)))
    _swap(QDesktopServices, "openUrl", staticmethod(lambda url: True))


class PageMonkey:
    PAGES = ["chat", "hw", "eval", "team", "set", "flow"]

    def __init__(self, window, rng: random.Random):
        self.w, self.rng = window, rng
        self.history: list[str] = []

    def _process(self, n: int = 3):
        from PySide6.QtWidgets import QApplication
        for _ in range(n):
            QApplication.processEvents()

    def _visible(self, cls):
        page = self.w.stack.currentWidget()
        return [x for x in page.findChildren(cls) if x.isVisible() and x.isEnabled()]

    def step(self) -> str | None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLineEdit, QListWidget, QPlainTextEdit, QSpinBox,
                                       QDoubleSpinBox, QSplitter, QTableWidget, QTabWidget, QTreeWidget)
        r = self.rng.random()
        if r < 0.1:
            key = self.rng.choice(self.PAGES)
            self.w.go(key)
            return f"切到 {key}"
        if r < 0.35:
            bs = [b for b in self._visible(QAbstractButton) if b.text() not in ("", )]
            if not bs:
                return None
            b = self.rng.choice(bs)
            QTest.mouseClick(b, Qt.LeftButton)
            return f"点按钮「{b.text()[:12]}」"
        if r < 0.5:
            cs = self._visible(QComboBox)
            if not cs:
                return None
            c = self.rng.choice(cs)
            if c.count() == 0:
                return None
            i = self.rng.randrange(c.count())
            text = c.itemText(i)[:14]                       # 先记下文字：选了以后界面可能重建，下拉框被删掉
            c.setCurrentIndex(i)
            c.activated.emit(i)
            return f"下拉框选「{text}」"
        if r < 0.6:
            es = self._visible(QLineEdit) + self._visible(QPlainTextEdit)
            if not es:
                return None
            e = self.rng.choice(es)
            text = self.rng.choice(["", "max30102", "uart,i2c", "测试 123", "x" * 200, "/task ledc_pwm", "，,；", "0"])
            e.setFocus()
            if isinstance(e, QLineEdit):
                e.setText(text)
                e.editingFinished.emit()
            else:
                e.setPlainText(text)
            return f"输入框填「{text[:12]}」"
        if r < 0.7:
            ls = self._visible(QListWidget) + self._visible(QTreeWidget) + self._visible(QTableWidget)
            if not ls:
                return None
            v = self.rng.choice(ls)
            vp = v.viewport()
            p = QPoint(self.rng.randrange(max(1, vp.width())), self.rng.randrange(max(1, vp.height())))
            QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, p)
            if self.rng.random() < 0.3:
                QTest.mouseDClick(vp, Qt.LeftButton, Qt.NoModifier, p)
            return f"点 {type(v).__name__} 的 ({p.x()},{p.y()})"
        if r < 0.78:
            ts = self._visible(QTabWidget)
            if not ts:
                return None
            t = self.rng.choice(ts)
            vis = [i for i in range(t.count()) if t.isTabVisible(i)]
            if not vis:
                return None
            i = self.rng.choice(vis)
            t.setCurrentIndex(i)
            return f"切标签「{t.tabText(i)}」"
        if r < 0.86:
            ss = self._visible(QSpinBox) + self._visible(QDoubleSpinBox)
            if not ss:
                return None
            s = self.rng.choice(ss)
            s.setValue(self.rng.uniform(s.minimum(), s.maximum()) if isinstance(s, QDoubleSpinBox)
                       else self.rng.randint(s.minimum(), s.maximum()))
            return f"数字框设成 {s.value()}"
        if r < 0.94:
            sps = self._visible(QSplitter)
            if not sps:
                return None
            sp = self.rng.choice(sps)
            n = sp.count()
            sizes = [self.rng.randint(0, 900) for _ in range(n)]
            sp.setSizes(sizes)
            return f"拖分隔条到 {sizes}"
        W, H = self.rng.choice([(1200, 760), (1440, 900), (1920, 1080), (1300, 800)])
        self.w.resize(W, H)
        return f"窗口改成 {W}×{H}"

    def problems(self) -> list[str]:
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QFrame
        out = []
        page = self.w.stack.currentWidget()
        for p in page.findChildren(QFrame, "Panel"):
            if not p.isVisible() or p.width() == 0:
                continue
            tl = p.mapTo(self.w, QPoint(0, 0))
            if tl.x() + p.width() > self.w.width() + 1 or tl.y() + p.height() > self.w.height() + 1:
                out.append(f"面板超出窗口：x={tl.x()} 宽={p.width()} 窗口宽={self.w.width()}")
        return out


def run_seed(window, seed: int, steps: int, errors: Errors) -> list[str]:
    rng = random.Random(seed)
    fake_world(rng)
    m = PageMonkey(window, rng)
    for i in range(steps):
        try:
            what = m.step()
        except Exception:                                   # noqa: BLE001
            return [f"种子 {seed} 第 {i + 1} 步出异常：\n" + traceback.format_exc()[-900:]]
        m._process()
        if what is None:
            continue
        m.history.append(what)
        bad = m.problems()
        if errors.items:
            bad.append("没接住的异常：\n" + errors.items.pop(0))
        if bad:
            recent = "\n    ".join(m.history[-8:])
            return [f"种子 {seed} 第 {i + 1} 步（{what}）之后：" + "；".join(bad[:3]) + f"\n  最近几步：\n    {recent}"]
    return []


def stop_backends(window):
    """停掉猴子点出来的后台运行，等所有后台线程结束（和关窗口时做的一样）。"""
    from PySide6.QtWidgets import QApplication
    from firmforge.gui.runner import shutdown_workers
    for owner in (window.chat, window.hardware, window.evalp):
        b = getattr(owner, "backend", None)
        if b is not None:
            try:
                b.stop()
            except Exception:                               # noqa: BLE001
                pass
    shutdown_workers(5000)
    QApplication.processEvents()


def main(argv=None) -> int:
    import argparse
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0-4")
    ap.add_argument("--steps", type=int, default=200)
    a = ap.parse_args(argv)
    lo, _, hi = a.seeds.partition("-")
    sandbox = make_sandbox()
    os.chdir(sandbox)
    os.environ["FF_HOME"] = str(sandbox)
    os.environ["HOME"] = str(sandbox)
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("FirmForgePageMonkey")
    QSettings().clear()
    errors = Errors()
    from firmforge.gui.app import MainWindow
    bad = 0
    for s in range(int(lo), int(hi or lo) + 1):
        w = MainWindow(sandbox)
        w.apply_theme()
        w.resize(1440, 900)
        w.show()
        found = run_seed(w, s, a.steps, errors)
        stop_backends(w)
        print(f"种子 {s}：{'没发现问题' if not found else '发现问题'}")
        for f in found:
            bad += 1
            print("  " + f)
        w.close()
        w.deleteLater()
        app.processEvents()
    shutil.rmtree(sandbox, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
