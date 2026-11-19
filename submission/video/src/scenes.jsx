import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "./components/Backdrop";
import { Rise } from "./components/Text";
import { C, FONT, MONO, sec } from "./theme";

// 开场：黑底逐行大字（痛点）→ 一句反问 → 名字
export const Intro = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const brand = spring({ frame: f - sec(8.2), fps, config: { damping: 200 } });
  const sweep = interpolate(f, [sec(8.6), sec(10)], [-30, 130], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Backdrop glow={f > sec(8)}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", gap: 28 }}>
        {f < sec(5.2) && (
          <>
            <Rise delay={sec(0.3)} size={84} out={sec(4.6)}>AI 写的固件，编译通过了。</Rise>
            <Rise delay={sec(2.2)} size={84} color={C.fail} out={sec(4.6)}>上板一跑，错了。</Rise>
          </>
        )}
        {f >= sec(5.2) && f < sec(8.1) && (
          <Rise delay={sec(5.3)} size={76} out={sec(7.6)}>如果对错不由 AI 说了算？</Rise>
        )}
        {f >= sec(8.1) && (
          <div style={{ textAlign: "center", opacity: brand, transform: `scale(${0.94 + 0.06 * brand})` }}>
            <div style={{ position: "relative", fontFamily: FONT, fontSize: 150, fontWeight: 800, color: C.ink, letterSpacing: "0.06em",
              backgroundImage: `linear-gradient(100deg, ${C.ink} ${sweep - 15}%, ${C.cyan} ${sweep}%, ${C.ink} ${sweep + 15}%)`,
              WebkitBackgroundClip: "text", backgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              固件工坊
            </div>
            <div style={{ fontFamily: FONT, fontSize: 40, color: C.muted, letterSpacing: "0.5em", marginTop: 8 }}>FIRMFORGE</div>
            <div style={{ fontFamily: FONT, fontSize: 38, color: C.ink, marginTop: 36, opacity: 0.9 }}>
              面向嵌入式固件开发的多 Agent 编排与验证平台
            </div>
          </div>
        )}
      </AbsoluteFill>
    </Backdrop>
  );
};

// 章节卡：编号 + 标题 + 一句说明，左侧竖线
export const Chapter = ({ no, title, sub }) => (
  <Backdrop>
    <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 220 }}>
      <Rise delay={2} size={40} color={C.cyan} weight={500} style={{ fontFamily: MONO }}>{no}</Rise>
      <div style={{ borderLeft: `6px solid ${C.blueHi}`, paddingLeft: 36, marginTop: 18 }}>
        <Rise delay={6} size={96}>{title}</Rise>
        {sub && <Rise delay={14} size={40} color={C.muted} weight={400} style={{ marginTop: 18 }}>{sub}</Rise>}
      </div>
    </AbsoluteFill>
  </Backdrop>
);

// 数字从 from 滚到 to
export const Count = ({ from = 0, to, delay = 0, dur = 36, suffix = "", decimals = 0, size = 140, color = C.ink }) => {
  const f = useCurrentFrame();
  const v = interpolate(f, [delay, delay + dur], [from, to], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (t) => 1 - Math.pow(1 - t, 3),
  });
  return <span style={{ fontFamily: FONT, fontSize: size, fontWeight: 800, color, fontVariantNumeric: "tabular-nums" }}>
    {v.toFixed(decimals)}{suffix}</span>;
};

// 数据页：四个大数字
export const Stats = () => {
  const items = [
    { el: <><Count from={39} to={67} delay={10} suffix="%" color={C.pass} /></>, cap: "难题通过率（改进前 39%）" },
    { el: <><Count from={9} to={0} delay={22} /><span style={{ fontSize: 60, color: C.muted }}> 次</span></>, cap: "把硬件问题误判、提前放弃（原来 9 次）" },
    { el: <><Count to={22} delay={34} /><span style={{ fontSize: 60, color: C.muted }}> 分钟</span></>, cap: "一句话到真板验证交付" },
    { el: <><Count to={0.86} decimals={2} delay={46} /><span style={{ fontSize: 60, color: C.muted }}> 元</span></>, cap: "一次真实需求的模型费用" },
  ];
  return (
    <Backdrop>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "70px 160px" }}>
          {items.map((it, i) => (
            <div key={i} style={{ textAlign: "center" }}>
              <div>{it.el}</div>
              <Rise delay={14 + i * 12} size={34} color={C.muted} weight={400}>{it.cap}</Rise>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Backdrop>
  );
};

// 片尾
export const Outro = () => (
  <Backdrop>
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}>
      <Rise delay={4} size={64}>AI 负责干活，对错交给客观检查。</Rise>
      <Rise delay={30} size={120} weight={800} style={{ marginTop: 60 }}>固件工坊</Rise>
      <Rise delay={40} size={34} color={C.muted} weight={400} style={{ letterSpacing: "0.4em" }}>FIRMFORGE</Rise>
      <Rise delay={56} size={32} color={C.ink} weight={400} style={{ marginTop: 50 }}>kami之队 · 集美大学诚毅学院</Rise>
      <Rise delay={64} size={30} color={C.cyan} weight={400} style={{ marginTop: 14, fontFamily: MONO }}>github.com/z-xxkk/firmforge</Rise>
    </AbsoluteFill>
  </Backdrop>
);
