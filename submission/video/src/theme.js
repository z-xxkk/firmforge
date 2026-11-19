// 视频的统一风格：和 PPT 同一套主色；通过用绿、没通过用红；字体用思源黑体（系统里是 Noto Sans CJK SC）
export const C = {
  bg0: "#050B1F",
  bg1: "#0A1A4A",
  blue: "#0A3AB8",
  blueHi: "#3D7BFF",
  cyan: "#5CE1FF",
  ink: "#F2F5FF",
  muted: "#9AA6C8",
  pass: "#2BD47D",
  fail: "#FF5A5F",
  warn: "#FFB547",
};

export const FONT = "'Noto Sans CJK SC', 'Source Han Sans SC', 'PingFang SC', sans-serif";
export const MONO = "'JetBrains Mono', 'Noto Sans Mono CJK SC', 'DejaVu Sans Mono', monospace";
export const FPS = 30;
export const sec = (s) => Math.round(s * FPS);
