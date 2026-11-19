import React from "react";
import { Audio, Composition, registerRoot, Sequence, staticFile, interpolate, useCurrentFrame } from "remotion";
import { Chapter, Intro, Outro, Stats } from "./scenes";
import { FPS, sec } from "./theme";

// 时间线：素材录好之前先放片头、章节卡、数据页、片尾，录屏段落之后按分镜补进来
const PARTS = [
  { c: <Intro />, d: 12 },
  { c: <Chapter no="01" title="一句话需求" sub="你只需要描述想让板子做什么" />, d: 3 },
  { c: <Chapter no="02" title="接线，平台自己测" sub="不相信“接好了”，烧一个自检程序去实测" />, d: 3 },
  { c: <Stats />, d: 7 },
  { c: <Outro />, d: 7 },
];

const Music = ({ total }) => {
  const f = useCurrentFrame();
  const v = interpolate(f, [0, 30, total - 60, total], [0, 0.55, 0.55, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <Audio src={staticFile("assets/music/pixabay_153473.mp3")} volume={v} />;
};

const Demo = () => {
  let at = 0;
  const total = PARTS.reduce((s, p) => s + sec(p.d), 0);
  return (
    <>
      {PARTS.map((p, i) => {
        const from = at;
        at += sec(p.d);
        return <Sequence key={i} from={from} durationInFrames={sec(p.d)}>{p.c}</Sequence>;
      })}
      <Music total={total} />
    </>
  );
};

const Root = () => (
  <Composition id="Demo" component={Demo} durationInFrames={PARTS.reduce((s, p) => s + sec(p.d), 0)}
    fps={FPS} width={1920} height={1080} />
);

registerRoot(Root);
