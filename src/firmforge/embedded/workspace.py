"""嵌入式工作区：命令行和桌面软件共用的"读硬件目录、建流程、建评测"入口（DECISIONS 第 100 条）。

硬件目录里有 profile.json（硬件档案）、sources.toml（资料清单）、rules.json（规则库）。
资料原件在 .ff_sources/raw，取出的文字缓存在 .ff_sources/library。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import config
from ..grounding.idf import IdfTree
from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet
from ..grounding.sources import SourceLibrary, load_manifest
from ..grounding.svd import SvdDevice
from ..target.esp_idf import idf_path

SOURCES_RAW = Path(".ff_sources") / "raw"
SOURCES_CACHE = Path(".ff_sources") / "library"


class WorkspaceError(ValueError):
    """硬件目录或参数不对，说明怎么改。"""


@dataclass
class HwContext:
    hwdir: Path
    profile: HardwareProfile
    library: SourceLibrary
    store: RuleSet
    rules_path: Path
    idf_tree: IdfTree | None
    svd: SvdDevice | None


def load_hw(hw: str | Path, *, need_sources: bool = True, rules: str | Path | None = None,
            root: Path | None = None) -> HwContext:
    """读硬件目录。need_sources=False 时只读缓存里的资料（不核对原件指纹）。rules 给了就改用那份规则库。"""
    root = Path(root) if root is not None else Path.cwd()
    hwdir = Path(hw)
    if not (hwdir / "profile.json").is_file():
        raise WorkspaceError(f"{hwdir} 下没有 profile.json（硬件档案）")
    profile = HardwareProfile.load(hwdir / "profile.json")
    raw, cache = root / SOURCES_RAW, root / SOURCES_CACHE
    library = load_manifest(hwdir / "sources.toml", raw, cache) if need_sources else SourceLibrary.load(cache)
    rules_path = Path(rules) if rules else hwdir / "rules.json"
    if rules and not rules_path.is_file():
        raise WorkspaceError(f"找不到规则库 {rules_path}")
    store = RuleSet.load(rules_path)
    try:
        idf_tree = IdfTree(idf_path())
    except FileNotFoundError:
        idf_tree = None
    svd = None
    for d in library.docs.values():
        if d.kind == "svd" and d.pages:
            svd = SvdDevice.parse(d.pages[0])
    return HwContext(hwdir, profile, library, store, rules_path, idf_tree, svd)


def secrets_of(settings: config.Settings) -> tuple[str, ...]:
    import os
    key = os.environ.get(settings.llm.api_key_env, "")
    return (key,) if key else ()


def make_eval_context(hw: HwContext, tasks_root: Path, *, llm, settings: config.Settings, idf, board,
                      runs_root: Path, build_root: Path, max_attempts: int = 4, tier: str | None = "fast",
                      think: str | None = "low", subscribers: tuple[Callable[[dict], None], ...] = ()):
    """上板评测的上下文（DECISIONS 第 92–94 条）：判定规则固定，Agent 统一档位和思考强度。"""
    from ..agents.registry import default_registry
    from .board_eval import EvalContext
    from .board_task import judge_rules
    return EvalContext(llm=llm, settings=settings, profile=hw.profile, judge=judge_rules(tasks_root, hw.profile),
                       rules=hw.store, library=hw.library, idf=idf, board=board, runs_root=runs_root,
                       build_root=build_root, max_attempts=max_attempts,
                       registry=default_registry().with_overrides(tier, think), secrets=secrets_of(settings),
                       subscribers=subscribers)


def make_firmware_flow(hw: HwContext, spec: str, *, llm, settings: config.Settings, rule_mode: str, idf, board,
                       runs_root: Path, name: str, human=None, tier: str | None = None, think: str | None = None,
                       subscribers: tuple[Callable[[dict], None], ...] = (), workflow: str | None = "firmware_dev",
                       store=None, human_review: bool = False, confirm_design=None, prior_wiring=None,
                       confirm_module=None, project=None):
    """嵌入式新流程（DECISIONS 第 66 条）：verified 模式带规则判定器，规则缺了会查资料、等人确认。
    workflow：上板工作流的名字或路径（DECISIONS 第 110 条），None 表示照原来的固定做法；
    store：本地数据库（调度器的成绩、经验库），None 时调度器只按标价和探索打分、不用经验库。"""
    from ..agents.registry import default_registry
    from ..grounding.research import llm_semantic_checker
    from ..grounding.verifier import RuleVerifier
    from ..target.esp_idf import PRODUCT_REQUIRES
    from .flow import EmbeddedFlow
    reg = default_registry().with_overrides(tier, think) if (tier or think) else default_registry()
    verifier = None
    if rule_mode == "verified":
        chk = reg.get("rule_checker")
        t = settings.tier(chk.model_tier)
        verifier = RuleVerifier(hw.library, hw.profile, idf=hw.idf_tree, svd=hw.svd,
                                semantic=llm_semantic_checker(llm, t.name, max_tokens=chk.max_tokens or t.max_tokens or 8192,
                                                              extra_body=t.extra_body(chk.thinking) or None))
    wf = None
    if workflow:
        from ..orchestrator.workflow import WorkflowError, load_workflow
        wf = load_workflow(workflow)
        if wf.engine != "firmware":
            raise WorkflowError(f"工作流 {wf.name} 不是上板流程用的（engine = {wf.engine}）")
    return EmbeddedFlow(llm=llm, settings=settings, profile=hw.profile, spec=spec, rules=hw.store,
                        library=hw.library if rule_mode != "none" else None, verifier=verifier, runs_root=runs_root,
                        name=name, rule_mode=rule_mode, human=human, idf=idf, board=board, registry=reg,
                        subscribers=subscribers, secrets=secrets_of(settings), workflow=wf, store=store,
                        human_review=human_review, confirm_design=confirm_design, prior_wiring=prior_wiring, project=project,
                        confirm_module=confirm_module, extra_requires=PRODUCT_REQUIRES,
                        rules_path=hw.rules_path if rule_mode == "verified" else None)


def make_rule_builder(hw: HwContext, *, llm, settings: config.Settings, runs_root: Path, human=None,
                      tier: str | None = None, think: str | None = None, max_rounds: int = 4,
                      subscribers: tuple[Callable[[dict], None], ...] = ()):
    """查资料提炼规则（DECISIONS 第 66 条第 ②③ 步）。返回 (规则构建器, 计费器, 运行目录)。
    每次调用都写调用日志；需要人确认时先把待确认的规则原样存成 pending_rules.json，再交给 human。"""
    import time
    from ..agents.registry import default_registry
    from ..grounding.pipeline import RuleBuilder
    from ..grounding.research import llm_semantic_checker
    from ..grounding.verifier import RuleVerifier
    from ..llm import CallLogger, LoggedLLM, MeteredLLM
    from ..orchestrator.events import EventBus
    from ..runs import RunDir
    reg = default_registry().with_overrides(tier, think)
    res_agent, chk_agent = reg.get("researcher"), reg.get("rule_checker")
    res_tier, chk_tier = settings.tier(res_agent.model_tier), settings.tier(chk_agent.model_tier)
    tiers = {t.name: t for t in (res_tier, chk_tier)}
    meter = MeteredLLM(llm, lambda model, pt, ct: tiers[model].cost(
        pt, ct, ts=time.time(), schedule=settings.pricing) if model in tiers else 0.0)
    rd = RunDir.create(Path(runs_root), "rules")
    logger = CallLogger(rd.path, list(secrets_of(settings)))
    res_llm, chk_llm = LoggedLLM(meter, logger, "researcher"), LoggedLLM(meter, logger, "rule_checker")

    def complete(m, tools):
        return res_llm.complete(m, model=res_tier.name, tools=tools, temperature=res_agent.temperature,
                                max_tokens=res_agent.max_tokens or res_tier.max_tokens or 8192,
                                extra_body=res_tier.extra_body(res_agent.thinking) or None)
    semantic = llm_semantic_checker(chk_llm, chk_tier.name, max_tokens=chk_agent.max_tokens or chk_tier.max_tokens or 8192,
                                    extra_body=chk_tier.extra_body(chk_agent.thinking) or None)
    bus = EventBus(rd.run_id, rd.events_path)
    for s in subscribers:
        bus.subscribe(s)
    verifier = RuleVerifier(hw.library, hw.profile, idf=hw.idf_tree, svd=hw.svd, semantic=semantic)

    def recorded_human(pending, escalated):
        # 先把要确认的规则原样存下来：事后能查当时给人看的是什么
        rd.write_json("pending_rules.json", {"pending": [r.to_dict() for r in pending],
                                             "escalated": [{"rule": x.rule.to_dict(), "reasons": x.reasons()}
                                                           for x in escalated]})
        return human(pending, escalated)

    builder = RuleBuilder(complete, hw.profile, hw.library, verifier, hw.store, emit=bus.emit,
                          human=recorded_human if human is not None else None, max_rounds=max_rounds)
    return builder, meter, rd
