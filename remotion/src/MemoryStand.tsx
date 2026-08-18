// SPDX-License-Identifier: Apache-2.0
// Presenter-led proof cut. The Grok talking-head carries the narration; selected
// beats add reviewed product footage so the required functioning project and
// CockroachDB memory layer are visible rather than merely named.

import React from 'react';
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  interpolate,
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
type Broll = {
  source: string;
  startSeconds: number;
  durationSeconds: number;
  label: string;
  headline: string;
  callouts: string[];
};
type Shot = {
  id: string;
  side: 'LEFT' | 'RIGHT';
  clip: string;
  durationFrames: number;
  panel?: {headline?: string};
  broll?: Broll | null;
  cues: Cue[];
};

const Captions: React.FC<{list: Cue[]; evidence?: boolean}> = ({
  list,
  evidence = false,
}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const cue = list.find((c) => t >= c.s && t < c.e);
  if (!cue) return null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: evidence ? 42 : 56,
        left: evidence ? '50%' : 0,
        transform: evidence ? 'translateX(-50%)' : undefined,
        width: evidence ? 1200 : '100%',
        minHeight: evidence ? 94 : undefined,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        pointerEvents: 'none',
        backgroundColor: evidence ? 'rgba(3, 6, 10, 0.98)' : undefined,
        border: evidence ? '1px solid rgba(255,255,255,0.18)' : undefined,
        borderRadius: evidence ? 18 : undefined,
        boxShadow: evidence ? '0 16px 36px rgba(0,0,0,0.42)' : undefined,
        padding: evidence ? '12px 28px 14px' : undefined,
      }}
    >
      <div
        style={{
          maxWidth: evidence ? '100%' : '74%',
          textAlign: 'center',
          fontSize: evidence ? 34 : 38,
          lineHeight: 1.28,
          fontWeight: 700,
          color: C.ink,
          backgroundColor: evidence ? 'transparent' : 'rgba(3, 6, 10, 0.78)',
          border: evidence ? 'none' : '1px solid rgba(255,255,255,0.14)',
          borderRadius: evidence ? 0 : 14,
          padding: evidence ? 0 : '12px 24px 14px',
          boxShadow: evidence ? 'none' : '0 12px 30px rgba(0,0,0,0.28)',
          textShadow: '0 2px 12px rgba(0,0,0,0.9)',
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

const EvidenceCard: React.FC<{broll: Broll}> = ({broll}) => (
  <AbsoluteFill style={{backgroundColor: C.bg}}>
    <OffthreadVideo
      src={staticFile('evidence.mp4')}
      startFrom={Math.round(broll.startSeconds * FPS)}
      volume={0}
      style={{
        width: '100%',
        height: '100%',
        objectFit: 'cover',
      }}
    />
    <AbsoluteFill
      style={{
        background:
          'linear-gradient(90deg, rgba(3,6,10,0.04) 0%, rgba(3,6,10,0.02) 52%, rgba(3,6,10,0.90) 100%)',
      }}
    />
    <div
      style={{
        position: 'absolute',
        // The reviewed evidence source has its own candidate-only badge in the
        // upper-right corner. Keep authored proof labels below that baked safe
        // area instead of making two independently valid labels unreadable.
        top: 82,
        right: 56,
        width: 560,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        textAlign: 'right',
      }}
    >
      <div
        style={{
          fontSize: 18,
          letterSpacing: 3,
          fontWeight: 800,
          color: C.accent,
        }}
      >
        {broll.label}
      </div>
      <div
        style={{
          marginTop: 12,
          fontSize: 40,
          lineHeight: 1.08,
          fontWeight: 820,
          color: C.ink,
          textShadow: '0 4px 20px rgba(0,0,0,0.85)',
        }}
      >
        {broll.headline}
      </div>
      <div style={{marginTop: 18, display: 'flex', flexDirection: 'column', gap: 10}}>
        {broll.callouts.map((item) => (
          <div
            key={item}
            style={{
              padding: '9px 14px',
              borderRadius: 10,
              backgroundColor: 'rgba(5,10,16,0.82)',
              border: '1px solid rgba(77,163,255,0.48)',
              color: C.ink,
              fontSize: 21,
              fontWeight: 650,
              boxShadow: '0 8px 22px rgba(0,0,0,0.24)',
            }}
          >
            {item}
          </div>
        ))}
      </div>
    </div>
  </AbsoluteFill>
);

const PresenterPictureInPicture: React.FC<{shot: Shot}> = ({shot}) => {
  const left = shot.side === 'LEFT' ? 54 : undefined;
  const right = shot.side === 'RIGHT' ? 54 : undefined;
  return (
    <div
      style={{
        position: 'absolute',
        left,
        right,
        bottom: 166,
        width: 410,
        height: 223,
        overflow: 'hidden',
        borderRadius: 20,
        border: '2px solid rgba(255,255,255,0.28)',
        boxShadow: '0 20px 48px rgba(0,0,0,0.46)',
        backgroundColor: C.bg,
      }}
    >
      <OffthreadVideo
        src={staticFile(shot.clip)}
        volume={1}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: 'center center',
        }}
      />
    </div>
  );
};

const ShotView: React.FC<{shot: Shot}> = ({shot}) => {
  const kicker = (shot.panel?.headline || '').replace(/\n/g, ' ').replace(/\.$/, '');
  if (shot.broll) {
    return (
      <AbsoluteFill style={{backgroundColor: C.bg}}>
        <EvidenceCard broll={shot.broll} />
        <PresenterPictureInPicture shot={shot} />
        <Captions list={shot.cues} evidence />
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <OffthreadVideo
        src={staticFile(shot.clip)}
        volume={1}
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
