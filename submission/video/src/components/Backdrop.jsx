import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { C } from "../theme";

// 深色渐变底 + 慢慢移动的光斑 + 很淡的网格：所有录屏和文字都放在这上面
export const Backdrop = ({ children, glow = true }) => {
  const f = useCurrentFrame();
  const x = interpolate(Math.sin(f / 140), [-1, 1], [30, 70]);
  const y = interpolate(Math.cos(f / 170), [-1, 1], [25, 60]);
  return (
    <AbsoluteFill style={{ background: `radial-gradient(1200px 800px at ${x}% ${y}%, ${C.bg1} 0%, ${C.bg0} 70%)` }}>
      {glow && (
        <AbsoluteFill
          style={{
            backgroundImage:
              "linear-gradient(rgba(92,225,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(92,225,255,0.05) 1px, transparent 1px)",
            backgroundSize: "64px 64px",
            maskImage: "radial-gradient(ellipse at center, black 30%, transparent 75%)",
          }}
        />
      )}
      {children}
    </AbsoluteFill>
  );
};
