import React from "react";
import { AbsoluteFill, interpolate, Easing, OffthreadVideo, Img, staticFile, useCurrentFrame } from "remotion";

// 录屏放在圆角框里、带阴影，可按关键帧慢慢推近到某一块
// zooms: [{at: 帧, scale: 1.6, x: 0.7, y: 0.3}]，x、y 是推近中心在画面里的比例（0–1）
// src 以 "assets/" 开头（public/assets 链接到 素材/）；still=true 时按图片处理（截图）
export const ScreenClip = ({ src, startFrom = 0, playbackRate = 1, zooms = [], still = false, width = 1680 }) => {
  const f = useCurrentFrame();
  const keys = [{ at: 0, scale: 1, x: 0.5, y: 0.5 }, ...zooms];
  const pick = (k) => {
    const frames = keys.map((z) => z.at);
    const vals = keys.map((z) => z[k]);
    if (frames.length === 1) return vals[0];
    return interpolate(f, frames, vals, {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.65, 0, 0.35, 1),
    });
  };
  const s = pick("scale");
  const cx = pick("x");
  const cy = pick("y");
  const h = (width * 9) / 16;
  const media = still ? (
    <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
  ) : (
    <OffthreadVideo src={staticFile(src)} startFrom={startFrom} playbackRate={playbackRate} muted
      style={{ width: "100%", height: "100%", objectFit: "cover" }} />
  );
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          width, height: h, borderRadius: 18, overflow: "hidden",
          boxShadow: "0 40px 120px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.08)",
        }}
      >
        <div style={{ width: "100%", height: "100%", transformOrigin: `${cx * 100}% ${cy * 100}%`, transform: `scale(${s})` }}>
          {media}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 右上角的加速标记，表示这段是加速播放、没有剪掉
export const SpeedBadge = ({ rate }) => (
  <div style={{ position: "absolute", top: 48, right: 72, fontFamily: "monospace", fontSize: 30, color: "#5CE1FF",
    padding: "6px 16px", borderRadius: 10, border: "1px solid rgba(92,225,255,0.5)", background: "rgba(5,11,31,0.6)" }}>
    ×{rate}
  </div>
);
