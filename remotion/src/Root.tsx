import React from 'react';
import {Composition} from 'remotion';
import {MemoryStand} from './MemoryStand';
import story from './story.json';

export const FPS = story.fps || 24;

const shotFrames = (story.shots || []).reduce(
  (sum: number, shot: {durationFrames: number}) => sum + (shot.durationFrames || 0),
  0,
);

export const DURATION_IN_FRAMES = Math.max(1, shotFrames + (story.outroFrames || 72));

export const RemotionRoot: React.FC = () => (
  <Composition
    id="MemoryStand"
    component={MemoryStand}
    durationInFrames={DURATION_IN_FRAMES}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
