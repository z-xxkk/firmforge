import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT } from "../theme";

// 一行字从下往上淡入（可选模糊消散），用于开场的逐行大字和章节标题
export const Rise = ({ children, delay = 0, size = 72, color = C.ink, weight = 700, out = null, style = {} }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - delay, fps, config: { damping: 200, mass: 0.8 } });
  let o = p;
  if (out !== null) o *= interpolate(f, [out, out + 12], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div
      style={{
        fontFamily: FONT, fontSize: size, fontWeight: weight, color, letterSpacing: "0.02em",
        opacity: o, transform: `translateY(${(1 - p) * 28}px)`, filter: `blur(${(1 - p) * 8}px)`, ...style,
      }}
    >
      {children}
    </div>
  );
};

// 底部字幕：半透明底条，淡入淡出；每句尽量不超过 15 个字
export const Caption = ({ text, from, to }) => {
  const f = useCurrentFrame();
  if (f < from || f > to) return null;
  const o = interpolate(f, [from, from + 8, to - 8, to], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 56, display: "flex", justifyContent: "center", opacity: o }}>
      <div
        style={{
          fontFamily: FONT, fontSize: 40, fontWeight: 500, color: C.ink, padding: "12px 28px", borderRadius: 14,
          background: "rgba(5,11,31,0.72)", backdropFilter: "blur(8px)", border: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {text}
      </div>
    </div>
  );
};
