// SPDX-License-Identifier: Apache-2.0
// MemoryStand submission cut — same Remotion layout contract as the earlier
// contest films: presenter (or live evidence) on one third, a large claim
// panel on the other, captions at the bottom. Grok only supplies the takes.

import React from 'react';
import {
  AbsoluteFill,
  Audio,
  interpolate,
  OffthreadVideo,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import story from './story.json';
import {FPS} from './Root';

const C = {
  bg: '#0a0e14',
  panel: 'rgba(18,24,34,0.94)',
  ink: '#eaeef4',
  dim: '#8b96a8',
  faint: '#5b6478',
  accent: '#4da3ff',
  amber: '#f5b93d',
  line: 'rgba(47,57,72,0.95)',
};

type Cue = {s: number; e: number; t: string};
type Panel = {
  kind: string;
  headline?: string;
  bullets?: string[];
  lines?: string[];
  footnote?: string;
};
type Broll = {
  source: string;
  startSeconds: number;
  label: string;
  headline: string;
};
type Shot = {
  id: string;
  side: 'LEFT' | 'RIGHT';
  clip: string;
  durationFrames: number;
  panel: Panel;
  broll: Broll | null;
  cues: Cue[];
};

const Captions: React.FC<{list: Cue[]}> = ({list}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const i = list.findIndex((c) => t >= c.s && t < c.e);
  if (i === -1) return null;
  const cue = list[i];
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 56,
        left: 0,
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          maxWidth: '72%',
          textAlign: 'center',
          fontSize: 40,
          lineHeight: 1.25,
          fontWeight: 700,
          color: '#fff',
          padding: '12px 28px',
          borderRadius: 12,
          background: 'rgba(4,9,16,0.9)',
          border: '1px solid rgba(120,160,205,0.18)',
        }}
      >
        {cue.t}
      </div>
    </div>
  );
};

const PanelView: React.FC<{panel: Panel; side: 'LEFT' | 'RIGHT'}> = ({panel, side}) => {
  const frame = useCurrentFrame();
  const enter = spring({frame, fps: FPS, config: {damping: 200, mass: 0.7}});
  const left = side === 'LEFT' ? 720 : 80;
  const headline = (panel.headline || '').split('\n');
  const bullets = panel.bullets || [];
  const lines = panel.lines || [];
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top: 160,
        width: 1120,
        opacity: enter,
        transform: `translateX(${interpolate(enter, [0, 1], [side === 'LEFT' ? 36 : -36, 0])}px)`,
        background: C.panel,
        border: `1px solid ${C.line}`,
        borderRadius: 18,
        padding: '42px 48px 36px',
      }}
    >
      {headline.map((line) => (
        <div
          key={line}
          style={{
            fontSize: 64,
            fontWeight: 850,
            color: C.amber,
            letterSpacing: -1,
            lineHeight: 0.98,
          }}
        >
          {line}
        </div>
      ))}
      {bullets.map((bullet) => (
        <div
          key={bullet}
          style={{
            marginTop: 22,
            fontSize: 36,
            fontWeight: 650,
            color: C.ink,
          }}
        >
          {bullet}
        </div>
      ))}
      {lines.map((line) => (
        <div
          key={line}
          style={{
            marginTop: 18,
            fontSize: 44,
            fontWeight: 800,
            color: C.ink,
            letterSpacing: 0.4,
          }}
        >
          {line}
        </div>
      ))}
      {panel.footnote ? (
        <div style={{marginTop: 28, fontSize: 20, color: C.faint, letterSpacing: 0.4}}>
          {panel.footnote}
        </div>
      ) : null}
    </div>
  );
};

const ShotView: React.FC<{shot: Shot}> = ({shot}) => {
  const onLeft = shot.side === 'LEFT';
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      {shot.broll ? (
        <>
          <Audio src={staticFile(shot.clip)} />
          <OffthreadVideo
            src={staticFile('evidence.mp4')}
            muted
            startFrom={Math.round(shot.broll.startSeconds * FPS)}
            style={{width: '100%', height: '100%', objectFit: 'cover'}}
          />
          <div
            style={{
              position: 'absolute',
              top: 36,
              left: 40,
              padding: '8px 16px',
              borderRadius: 8,
              background: 'rgba(4,9,16,0.82)',
              color: C.accent,
              fontSize: 18,
              fontWeight: 750,
              letterSpacing: 1.4,
            }}
          >
            {shot.broll.label}
          </div>
          <div
            style={{
              position: 'absolute',
              top: 84,
              left: 40,
              color: C.ink,
              fontSize: 34,
              fontWeight: 800,
              maxWidth: 980,
            }}
          >
            {shot.broll.headline}
          </div>
        </>
      ) : (
        <OffthreadVideo
          src={staticFile(shot.clip)}
          style={{
            position: 'absolute',
            left: onLeft ? 0 : 1260,
            top: 0,
            width: 660,
            height: 1080,
            objectFit: 'cover',
            objectPosition: onLeft ? 'left center' : 'right center',
          }}
        />
      )}
      {!shot.broll ? <PanelView panel={shot.panel} side={shot.side} /> : null}
      <Captions list={shot.cues} />
    </AbsoluteFill>
  );
};

const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const enter = spring({frame, fps: FPS, config: {damping: 200}});
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
      <div style={{textAlign: 'center', maxWidth: 1400}}>
        <div style={{fontSize: 56, fontWeight: 850, color: C.ink, lineHeight: 1.1}}>
          {outro.tagline}
        </div>
        <div
          style={{
            marginTop: 28,
            fontSize: 32,
            color: C.accent,
            fontFamily: 'ui-monospace, Menlo, monospace',
          }}
        >
          {outro.repository}
        </div>
        <div style={{marginTop: 18, fontSize: 22, color: C.dim}}>{outro.status}</div>
        <div style={{marginTop: 36, fontSize: 18, color: C.faint}}>{outro.disclosure}</div>
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
