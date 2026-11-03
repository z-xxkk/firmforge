"""上板评测题（tasks_board/<id>/，DECISIONS 第 82–84 条）：加载与自检。

目录结构和旧题一样：spec.md、include/（公共头文件，锁定）、reference/（标准答案）、mutants/（故意写错的版本）、
hidden_tests/（上板运行的 Unity 用例，只有评测流程能读，CLAUDE.md 硬性规则 5）、task.toml。

task.toml 多出的字段：
- target：目标芯片（esp32s3）
- wiring：用到的接线（硬件档案里的信号名），自检前先核对硬件档案里有这些信号
- requires：额外的 ESP-IDF 组件（例如 esp_driver_pcnt）
- scopes：涉及的外设和器件（规则适用范围，例如 ["i2c", "mpu6050"]）
- 变异体的 expect_fail：compliance（合规检查拦下）/ build（编译不过）/ board_test（隐藏测试抓住）

自检：
- 没有板子：标准答案和每个变异体都要能编译（expect_fail = build 的除外），合规检查只拦 expect_fail = compliance 的；
- 有板子：标准答案通过全部隐藏测试，每个 expect_fail = board_test 的变异体至少一个隐藏用例失败。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..grounding.compliance import check_code
from ..grounding.profile import HardwareProfile
from ..grounding.rules import Citation, Rule, RuleSet
from ..target.esp_idf import DEFAULT_REQUIRES, IdfEnv, IdfProject
from ..target.harness import TEST_REQUIRES, harness_files

BOARD_CATEGORIES = ("peripheral", "driver", "integration")
EXPECT = ("compliance", "build", "board_test")


class BoardTaskError(ValueError):
    """task.toml 内容不合法。"""


@dataclass(frozen=True)
class BoardMutant:
    file: str
    replaces: str
    expect_fail: str
    why: str = ""


@dataclass(frozen=True)
class BoardTask:
    id: str
    title: str
    category: str
    difficulty: int
    source: str
    root: Path
    target: str = "esp32s3"
    wiring: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    mutants: tuple[BoardMutant, ...] = ()
    scopes: tuple[str, ...] = ()       # 涉及的外设和器件（规则适用范围），评测时只把这些范围的规则给 Agent

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.md"

    @property
    def hidden_tests_dir(self) -> Path:
        return self.root / "hidden_tests"

    def public_files(self) -> dict[str, str]:
        """公共头文件（锁定）：main/include/ 下的相对路径 → 内容。"""
        return {f"include/{p.name}": p.read_text(encoding="utf-8") for p in sorted((self.root / "include").glob("*.h"))}

    def reference_files(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in sorted((self.root / "reference").glob("*.[ch]"))}

    def hidden_files(self) -> dict[str, str]:
        """只给评测流程用。"""
        return {f"hidden/{p.name}": p.read_text(encoding="utf-8") for p in sorted(self.hidden_tests_dir.glob("*.[ch]"))}

    def mutant_files(self, m: BoardMutant) -> dict[str, str]:
        files = self.reference_files()
        if m.replaces not in files:
            raise BoardTaskError(f"{self.id}：变异体 {m.file} 要替换的 {m.replaces} 不在 reference/ 里")
        files[m.replaces] = (self.root / "mutants" / m.file).read_text(encoding="utf-8")
        return files


def load_board_task(path: Path | str) -> BoardTask:
    root = Path(path)
    root = root.parent if root.name == "task.toml" else root
    f = root / "task.toml"
    if not f.is_file():
        raise BoardTaskError(f"找不到 {f}")
    try:
        d = tomllib.loads(f.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BoardTaskError(f"{f} 解析失败：{exc}") from exc
    for k in ("id", "title", "category", "difficulty"):
        if k not in d:
            raise BoardTaskError(f"{f} 缺少 {k}")
    if d["category"] not in BOARD_CATEGORIES:
        raise BoardTaskError(f"{f}：类别只能是 {', '.join(BOARD_CATEGORIES)}")
    muts = []
    for m in d.get("mutants", []):
        if m.get("expect_fail") not in EXPECT:
            raise BoardTaskError(f"{f}：变异体 {m.get('file')} 的 expect_fail 只能是 {', '.join(EXPECT)}")
        muts.append(BoardMutant(m["file"], m["replaces"], m["expect_fail"], m.get("why", "")))
    return BoardTask(d["id"], d["title"], d["category"], int(d["difficulty"]), d.get("source", ""), root.resolve(),
                     d.get("target", "esp32s3"), tuple(d.get("wiring", [])), tuple(d.get("requires", [])), tuple(muts),
                     tuple(d.get("scopes", [])))


def judge_rules(tasks_root: Path | str, profile: HardwareProfile) -> RuleSet:
    """评测判定用的合规规则：固定的 judge_rules.json，加上从硬件档案保留引脚生成的禁用引脚（DECISIONS 第 85、86 条）。
    与 Agent 自己的规则库无关，所以不同配置按同一把尺子打分。"""
    import json
    data = json.loads((Path(tasks_root) / "judge_rules.json").read_text(encoding="utf-8"))
    rs = RuleSet([Rule.from_dict(d) for d in data])
    if profile.reserved_pins:
        rs.put(Rule("R-JUDGE-PIN", "pin", ("all",), f"不许使用 {profile.board.value} 的保留引脚",
                    Citation("profile", None, "硬件档案 reserved_pins", "reserved_pins"), "user", "trusted",
                    {"type": "pin_forbidden", "gpio": sorted(profile.reserved_pins)}))
    return rs


def discover_board_tasks(root: Path | str) -> list[BoardTask]:
    return sorted((load_board_task(p.parent) for p in Path(root).glob("*/task.toml")), key=lambda t: t.id)


# ---------------------------------------------------------------- 没有板子时的自检

@dataclass
class VariantCheck:
    name: str                 # reference 或变异体文件名
    expect: str               # pass / compliance / build / board_test
    compliance_ok: bool = True
    build_ok: bool | None = None       # None：没编译（ESP-IDF 不可用）
    problems: list[str] = field(default_factory=list)

    @property
    def as_expected(self) -> bool:
        if self.expect == "compliance":
            return not self.compliance_ok
        if self.expect == "build":
            return self.compliance_ok and self.build_ok is False
        # pass、board_test：没有板子时只能要求合规通过、能编译
        return self.compliance_ok and self.build_ok is not False


@dataclass
class TaskCheck:
    task: str
    variants: list[VariantCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and all(v.as_expected for v in self.variants)


def check_without_board(task: BoardTask, profile: HardwareProfile, rules: RuleSet | None, idf: IdfEnv | None,
                        build_root: Path) -> TaskCheck:
    out = TaskCheck(task.id)
    missing = [w for w in task.wiring if w not in profile.pins]
    if missing:
        out.problems.append("硬件档案里没有这些信号：" + "、".join(missing))
    variants = [("reference", "pass", task.reference_files())]
    variants += [(m.file, m.expect_fail, task.mutant_files(m)) for m in task.mutants]
    proj = IdfProject(Path(build_root) / f"{task.id}-{profile.chip.value}", idf, target=str(profile.chip.value),
                      requires=DEFAULT_REQUIRES + TEST_REQUIRES + task.requires,
                      whole_archive=True) if idf is not None else None
    for name, expect, files in variants:
        v = VariantCheck(name, expect)
        comp = check_code(files, rules.select(None, "approved") if rules is not None else [])
        v.compliance_ok = comp.ok
        if not comp.ok:
            v.problems.append(comp.summary())
        if proj is not None and comp.ok:
            proj.write(profile, {**task.public_files(), **files, **task.hidden_files(), **harness_files(rules, None)})
            b = proj.build()
            v.build_ok = b.ok
            if not b.ok:
                v.problems += [d.render() for d in b.errors] or [b.log_tail[-800:]]
        out.variants.append(v)
    return out


# ---------------------------------------------------------------- 有板子时的自检

@dataclass
class BoardVariantResult:
    name: str
    expect: str
    verdict: str                      # pass / fail / skipped
    failed_cases: list[str] = field(default_factory=list)
    report: str = ""

    @property
    def as_expected(self) -> bool:
        if self.expect == "pass":
            return self.verdict == "pass"
        if self.expect == "board_test":
            return self.verdict == "fail"
        return True                    # compliance / build 类在没有板子的自检里已经判过


@dataclass
class JudgeResult:
    """评测判定：固定判定规则的合规检查 → 编译 → 上板跑隐藏测试。所有配置用同一把尺子。"""
    verdict: str                      # pass / fail / skipped
    failed_layer: str = ""            # compliance / build / board_test / skipped
    cases_passed: int = 0
    cases_total: int = 0
    failed_cases: list[str] = field(default_factory=list)
    report: str = ""


def hidden_case_count(task: BoardTask) -> int:
    import re
    return sum(len(re.findall(r"^TEST_CASE\(", c, re.M)) for c in task.hidden_files().values())


def judge_on_board(task: BoardTask, profile: HardwareProfile, judge: RuleSet | None, idf: IdfEnv | None, board,
                   files: dict[str, str], build_dir: Path, log_path: Path | None) -> JudgeResult:
    """files：被测代码（相对 main/ 的路径 → 内容），公共头文件一律用题目自带的（Agent 改了也不算）。"""
    total = hidden_case_count(task)
    files = {k: v for k, v in files.items() if k not in task.public_files()}
    comp = check_code(files, judge.select(None, "approved") if judge is not None else [])
    if not comp.ok:
        return JudgeResult("fail", "compliance", 0, total, [], comp.summary())
    if idf is None:
        return JudgeResult("skipped", "skipped", 0, total, [], "ESP-IDF 不可用，没有编译")
    proj = IdfProject(Path(build_dir), idf, target=str(profile.chip.value),
                      requires=DEFAULT_REQUIRES + TEST_REQUIRES + task.requires, whole_archive=True)
    proj.write(profile, {**task.public_files(), **files, **task.hidden_files(), **harness_files(judge, None)})
    b = proj.build()
    if not b.ok:
        return JudgeResult("fail", "build", 0, total, ["编译失败"],
                           "\n".join(d.render() for d in b.errors) or b.log_tail[-800:])
    if board is None:
        return JudgeResult("skipped", "skipped", 0, total, [], "没有板子，只编译了")
    r = board.run(proj, log_path)
    if r.verdict() == "skipped":
        return JudgeResult("skipped", "skipped", 0, total, [], r.report())
    passed = sum(1 for c in r.cases if c.status == "pass")
    return JudgeResult(r.verdict(), "" if r.ok else "board_test", passed, total, [c.name for c in r.failed], r.report())


def check_with_board(task: BoardTask, profile: HardwareProfile, rules: RuleSet | None, idf: IdfEnv, board,
                     build_root: Path, log_dir: Path, only_reference: bool = False) -> list[BoardVariantResult]:
    """标准答案必须通过全部隐藏用例；每个 expect_fail = board_test 的变异体至少一个用例失败。
    board：target.board.BoardRunner。串口日志存在 log_dir/<题目>/<变体>.log。
    这里跑的变体（标准答案、board_test 类变异体）都应通过合规检查，合规预期另在没有板子的自检里判。"""
    variants = [("reference", "pass", task.reference_files())]
    if not only_reference:
        variants += [(m.file, m.expect_fail, task.mutant_files(m)) for m in task.mutants if m.expect_fail == "board_test"]
    out = []
    for name, expect, files in variants:
        j = judge_on_board(task, profile, rules, idf, board, files,
                           Path(build_root) / f"{task.id}-{profile.chip.value}-board", Path(log_dir) / task.id / f"{name}.log")
        out.append(BoardVariantResult(name, expect, j.verdict, j.failed_cases, j.report))
    return out
