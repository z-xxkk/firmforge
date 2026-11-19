import React from "react";
import { AbsoluteFill, Audio, Composition, interpolate, registerRoot, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Backdrop } from "./components/Backdrop";
import { ScreenClip, SpeedBadge } from "./components/ScreenClip";
import { Caption } from "./components/Text";
import { Chapter, Intro, Outro, Stats } from "./scenes";
import { C, FONT, FPS, sec } from "./theme";

const R1 = "assets/转码/R1_主线.mp4";

// 一段录屏：src 从 from 秒开始，按 rate 倍速播放 dur 秒（成片里的时长）
// caps: [[成片里第几秒, 到第几秒, 字幕]]；zooms 的 at 也用成片秒
// mark: 大标记 {at, text, color}
const clip = ({ src = R1, from, rate = 1, dur, caps = [], zooms = [], mark = null, phone = false }) => ({
  d: dur,
  c: (
    <Backdrop>
      <ScreenClip src={src} startFrom={sec(from)} playbackRate={rate}
        zooms={zooms.map((z) => ({ ...z, at: sec(z.at) }))} width={phone ? 1440 : 1680} />
      {rate > 1.01 && <SpeedBadge rate={Math.round(rate * 10) / 10} />}
      {caps.map(([a, b, t], i) => <Caption key={i} text={t} from={sec(a)} to={sec(b)} />)}
      {mark && <Mark {...mark} />}
    </Backdrop>
  ),
});

// 画面上方的大标记（✗ 没应答 / ✓ 对上了）
const Mark = ({ at, text, color }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [sec(at), sec(at) + 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: 70, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: o }}>
      <div style={{ fontFamily: FONT, fontSize: 56, fontWeight: 800, color, padding: "10px 34px", borderRadius: 16,
        background: "rgba(5,11,31,0.85)", border: `2px solid ${color}`, transform: `scale(${0.9 + 0.1 * o})` }}>{text}</div>
    </div>
  );
};

const chapter = (no, title, sub, d = 2.6) => ({ d, c: <Chapter no={no} title={title} sub={sub} /> });

const PARTS = [
  { c: <Intro />, d: 12 },

  // 01 一句话需求
  chapter("01", "一句话需求", "你只需要描述想让板子做什么"),
  clip({ from: 4, rate: 8, dur: 14, zooms: [{ at: 0.8, scale: 1.7, x: 0.4, y: 0.88 }],
    caps: [[0.5, 13.5, "在对话框里，用一句话写下要实现的功能"]] }),
  clip({ from: 116, rate: 2, dur: 8,
    caps: [[0.3, 7.8, "平台先从规则库取出要用的硬件规则，再交给设计员"]] }),
  clip({ from: 132, rate: 2, dur: 11,
    zooms: [{ at: 0.6, scale: 1.7, x: 0.3, y: 0.22 }, { at: 5.5, scale: 1.7, x: 0.3, y: 0.22 }, { at: 6.8, scale: 1.6, x: 0.86, y: 0.3 }],
    caps: [[0.3, 5.6, "设计员拆成 5 个模块，写清接口、验收标准和接线"],
      [5.8, 10.8, "工程图自动画出：模块、芯片外设、外接器件"]] }),

  // 02 接线实测
  chapter("02", "接线，平台自己测", "不相信“接好了”，烧一个自检程序去实测"),
  clip({ from: 174, rate: 1, dur: 9, zooms: [{ at: 0.5, scale: 1.45, x: 0.35, y: 0.45 }],
    caps: [[0.3, 8.8, "写代码之前，先列出接线清单：每根线两头接哪里、为什么"]] }),
  clip({ src: "assets/转码/插错引脚.mp4", from: 2, rate: 1, dur: 3.6, phone: true,
    caps: [[0.1, 3.5, "演示：故意拔掉传感器的数据线（GPIO4）"]] }),
  clip({ from: 190, rate: 16, dur: 9.2,
    caps: [[0.2, 9, "点“已接好”后，平台编译并烧录一个自己写的自检程序"]] }),
  clip({ from: 336, rate: 1, dur: 9, zooms: [{ at: 0.4, scale: 1.8, x: 0.36, y: 0.78 }],
    mark: { at: 0.8, text: "✗ MPU6050 没有应答", color: C.fail },
    caps: [[1, 8.8, "实测发现传感器没应答：线没接好，马上拦下"]] }),
  clip({ src: "assets/转码/正确插入引脚.mp4", from: 2, rate: 1, dur: 3.6, phone: true,
    caps: [[0.1, 3.5, "把线插回去，再点一次"]] }),
  clip({ from: 352, rate: 4, dur: 7, zooms: [{ at: 4.5, scale: 1.6, x: 0.36, y: 0.8 }],
    mark: { at: 5.6, text: "✓ 0x68 应答，对上了", color: C.pass },
    caps: [[0.2, 6.8, "实测对上了，才开始写代码"]] }),

  // 03 分工、调度、返工
  chapter("03", "多个 AI 分工，平台逐层检查", "编译 → 上板测试 → 故意改坏代码 → 主程序上板"),
  clip({ from: 376, rate: 30, dur: 28.5, zooms: [{ at: 0, scale: 1.08, x: 0.6, y: 0.4 }],
    caps: [
      [0.2, 3.9, "每个模块：程序员写代码，测试员同时写测试，互相看不到"],
      [4.0, 7.0, "编译或上板没过 → 诊断员判断错在哪，交给对应的人改"],
      [7.1, 10.3, "上板时 MPU6050 真的应答了：工程图标“已实测确认”"],
      [10.4, 15.5, "故意把代码改错 3 处，测试一个都没发现 → 让测试员补测试"],
      [15.6, 20.3, "下一个模块同样：编译、上板、诊断、返工"],
      [20.4, 23.0, "改错 3 处只抓到 2 处 → 继续补测试"],
      [23.1, 24.6, "逻辑模块在电脑上测：改错 3 处全部抓到"],
      [24.7, 28.3, "模块全部通过 → 写主程序，整个工程上板跑两轮"],
    ] }),
  clip({ from: 1233, rate: 1, dur: 6.5, zooms: [{ at: 0.3, scale: 1.8, x: 0.3, y: 0.78 }],
    mark: { at: 0.6, text: "✓ 全部通过 · 0.83 元 · 19 分钟", color: C.pass },
    caps: [[1, 6.3, "一句话到真板子上验证过的完整工程"]] }),
  clip({ from: 1242, rate: 4, dur: 19,
    caps: [[0.3, 9.5, "点任何一个模块：每次尝试、诊断、上板输出、代码都在"],
      [9.6, 18.7, "“调度”标签：为什么这个模块交给这个 AI"]] }),
  clip({ from: 1318, rate: 2.5, dur: 10,
    caps: [[0.3, 9.7, "对话可以重命名、删除（移到回收站，能还原）"]] }),
  clip({ from: 1345, rate: 4, dur: 12,
    caps: [[0.3, 11.7, "导出完整工程：可以直接烧录的源码"]] }),

  // 04 平台能力
  chapter("04", "知识、团队与编排", "规则有出处 · 角色可管理 · 流程可视化"),
  clip({ src: "assets/转码/R2_知识库.mp4", from: 0, rate: 1.5, dur: 10,
    caps: [[0.3, 9.7, "知识库：每条规则带手册原文；手册和实测对不上会标出来"]] }),
  clip({ src: "assets/转码/R3_拖入手册.mp4", from: 0, rate: 1.4, dur: 7,
    caps: [[0.3, 6.8, "把芯片手册拖进对话，平台自动登记、算校验值"]] }),
  clip({ src: "assets/转码/R4_团队.mp4", from: 0, rate: 2, dur: 8,
    caps: [[0.3, 7.8, "AI 团队：每个角色有版本、模型档位、工具和历史成绩"]] }),
  clip({ src: "assets/转码/R5_工作流.mp4", from: 0, rate: 2, dur: 15,
    caps: [[0.3, 7.5, "工作流像画流程图一样编排：先后、同时、条件、循环、人工确认"],
      [7.6, 14.8, "“开始检查”：用真正的执行程序空跑几种情况"]] }),
  clip({ src: "assets/转码/R6_设置.mp4", from: 0, rate: 2.5, dur: 10,
    caps: [[0.3, 9.7, "设置：模型与密钥（只写不读）、工具，还能通过 MCP 接入其他 AI 软件"]] }),

  { c: <Stats />, d: 7.5 },
  { c: <Outro />, d: 7.5 },
];

const TOTAL = PARTS.reduce((s, p) => s + sec(p.d), 0);

// 背景音乐：两首接起来，交接处淡入淡出；整体音量偏低
const Music = () => {
  const A = sec(150);                     // 第一首 2:31，用到 2:30
  const va = (f) => interpolate(f, [0, 30, A - 60, A], [0, 0.45, 0.45, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const B = TOTAL - (A - 60);
  const vb = (f) => interpolate(f, [0, 60, B - 75, B], [0, 0.45, 0.45, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <>
      <Sequence from={0} durationInFrames={A}><Audio src={staticFile("assets/music/pixabay_153473.mp3")} volume={va} /></Sequence>
      <Sequence from={A - 60} durationInFrames={B}><Audio src={staticFile("assets/music/pixabay_613800.mp3")} volume={vb} /></Sequence>
    </>
  );
};

const Demo = () => {
  let at = 0;
  return (
    <AbsoluteFill style={{ background: C.bg0 }}>
      {PARTS.map((p, i) => {
        const from = at;
        at += sec(p.d);
        return <Sequence key={i} from={from} durationInFrames={sec(p.d)}>{p.c}</Sequence>;
      })}
      <Music />
    </AbsoluteFill>
  );
};

const Root = () => <Composition id="Demo" component={Demo} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />;

registerRoot(Root);
