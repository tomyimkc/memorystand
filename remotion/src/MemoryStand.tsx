// SPDX-License-Identifier: Apache-2.0
// Presenter-first cut. The Grok talking-head is the picture. No slide boards.
// No screen-capture takeover. Captions + a thin lower-third only.

import React from 'react';
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import story from './story.json';
import {FPS} from './Root';

const C = {
  bg: '#07090d',
  ink: '#f4f7fb',
  dim: '#b7c0cc',
  faint: '#8b96a8',
  accent: '#4da3ff',
};

type Cue = {s: number; e: number; t: string};
type Shot = {
  id: string;
  side: 'LEFT' | 'RIGHT';
  clip: string;
  durationFrames: number;
  panel?: {headline?: string};
  cues: Cue[];
};

const Captions: React.FC<{list: Cue[]}> = ({list}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const cue = list.find((c) => t >= c.s && t < c.e);
  if (!cue) return null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 48,
        left: 0,
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          maxWidth: '70%',
          textAlign: 'center',
          fontSize: 36,
          lineHeight: 1.28,
          fontWeight: 650,
          color: C.ink,
          textShadow: '0 2px 16px rgba(0,0,0,0.85)',
        }}
      >
        {cue.t}
      </div>
    </div>
  );
};

const LowerThird: React.FC<{label: string; side: 'LEFT' | 'RIGHT'}> = ({
  label,
  side,
}) => {
  if (!label) return null;
  const emptySide = side === 'LEFT' ? {right: 48} : {left: 48};
  return (
    <div
      style={{
        position: 'absolute',
        ...emptySide,
        bottom: 148,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        alignItems: side === 'LEFT' ? 'flex-end' : 'flex-start',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          fontSize: 14,
          letterSpacing: 2.4,
          fontWeight: 750,
          color: C.accent,
        }}
      >
        MEMORYSTAND
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 650,
          color: C.dim,
          letterSpacing: 0.2,
        }}
      >
        {label}
      </div>
    </div>
  );
};

const ShotView: React.FC<{shot: Shot}> = ({shot}) => {
  const kicker = (shot.panel?.headline || '').replace(/\n/g, ' ').replace(/\.$/, '');
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <OffthreadVideo
        src={staticFile(shot.clip)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: 'center center',
        }}
      />
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(180deg, rgba(7,9,13,0.12) 0%, rgba(7,9,13,0) 22%, rgba(7,9,13,0.08) 70%, rgba(7,9,13,0.48) 100%)',
        }}
      />
      <LowerThird label={kicker} side={shot.side} />
      <Captions list={shot.cues} />
    </AbsoluteFill>
  );
};

const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const enter = spring({frame, fps: FPS, config: {damping: 180}});
  const outro = story.outro;
  return (
    <AbsoluteFill
      style={{
        backgroundColor: C.bg,
        alignItems: 'center',
        justifyContent: 'center',
        opacity: enter,
      }}
    >
      <div style={{textAlign: 'center', maxWidth: 1280}}>
        <div style={{fontSize: 18, letterSpacing: 3, color: C.accent, fontWeight: 750}}>
          MEMORYSTAND
        </div>
        <div style={{marginTop: 18, fontSize: 42, fontWeight: 750, color: C.ink}}>
          {outro.tagline}
        </div>
        <div
          style={{
            marginTop: 22,
            fontSize: 26,
            color: C.accent,
            fontFamily: 'ui-monospace, Menlo, monospace',
          }}
        >
          {outro.repository}
        </div>
        <div style={{marginTop: 28, fontSize: 16, color: C.faint}}>{outro.disclosure}</div>
      </div>
    </AbsoluteFill>
  );
};

export const MemoryStand: React.FC = () => {
  const shots = (story.shots || []) as Shot[];
  let cursor = 0;
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      {shots.map((shot) => {
        const from = cursor;
        cursor += shot.durationFrames;
        return (
          <Sequence key={shot.id} from={from} durationInFrames={shot.durationFrames}>
            <ShotView shot={shot} />
          </Sequence>
        );
      })}
      <Sequence from={cursor} durationInFrames={story.outroFrames || 72}>
        <Outro />
      </Sequence>
    </AbsoluteFill>
  );
};
