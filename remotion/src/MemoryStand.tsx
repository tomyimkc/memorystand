// SPDX-License-Identifier: Apache-2.0
// Presenter-led proof cut. The Grok talking-head introduces each claim, then
// hands the full 1920x1080 frame to the reviewed evidence. Product text is
// never covered by presenter chrome, captions, headings, gradients, or callouts.

import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import story from './story.json';
import {FPS} from './Root';

const C = {
  bg: '#05080d',
  ink: '#f4f7fb',
  dim: '#b7c0cc',
  faint: '#8b96a8',
  accent: '#4da3ff',
};
const UI_FONT =
  'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

type Cue = {s: number; e: number; t: string};
type Broll = {
  source: string;
  startSeconds: number;
  durationSeconds: number;
  asset: string;
  kind: 'image' | 'video';
  label?: string;
  headline?: string;
  callouts?: string[];
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

const EVIDENCE_HANDOFF_S = 1.85;

const currentCue = (list: Cue[], frame: number) => {
  const t = frame / FPS;
  return list.find((cue) => t >= cue.s && t < cue.e);
};

const Captions: React.FC<{
  list: Cue[];
  evidence?: boolean;
}> = ({list, evidence = false}) => {
  const cue = currentCue(list, useCurrentFrame());
  if (!cue) return null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: evidence ? 0 : 56,
        left: 0,
        width: '100%',
        minHeight: evidence ? 74 : undefined,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        pointerEvents: 'none',
        backgroundColor: evidence ? 'rgba(2, 5, 9, 0.97)' : undefined,
        borderTop: evidence ? '1px solid rgba(255,255,255,0.16)' : undefined,
        padding: evidence ? '10px 44px 12px' : undefined,
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          maxWidth: evidence ? '92%' : '74%',
          textAlign: 'center',
          fontSize: evidence ? 32 : 38,
          lineHeight: 1.28,
          fontWeight: 700,
          fontFamily: UI_FONT,
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
          fontFamily: UI_FONT,
          color: C.accent,
        }}
      >
        MEMORYSTAND
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 650,
          fontFamily: UI_FONT,
          color: C.dim,
          letterSpacing: 0.2,
        }}
      >
        {label}
      </div>
    </div>
  );
};

const PresenterShot: React.FC<{
  shot: Shot;
  captions: boolean;
  volume?: number;
}> = ({shot, captions, volume = 1}) => {
  const kicker = (shot.panel?.headline || '').replace(/\n/g, ' ').replace(/\.$/, '');
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <OffthreadVideo
        src={staticFile(shot.clip)}
        volume={volume}
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
      {captions ? <Captions list={shot.cues} /> : null}
    </AbsoluteFill>
  );
};

const EvidenceShot: React.FC<{shot: Shot; broll: Broll}> = ({shot, broll}) => {
  const handoffFrame = Math.min(
    Math.max(1, shot.durationFrames - 1),
    Math.round(EVIDENCE_HANDOFF_S * FPS),
  );
  const evidenceFrames = Math.max(1, shot.durationFrames - handoffFrame);
  return (
    <AbsoluteFill style={{backgroundColor: C.bg}}>
      <Sequence from={0} durationInFrames={handoffFrame} premountFor={FPS}>
        <PresenterShot shot={shot} captions volume={0} />
      </Sequence>
      <Sequence
        from={handoffFrame}
        durationInFrames={evidenceFrames}
        premountFor={FPS}
      >
        <AbsoluteFill style={{backgroundColor: '#030507'}}>
          {broll.kind === 'image' ? (
            <Img
              src={staticFile(broll.asset)}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                objectPosition: 'center center',
              }}
            />
          ) : (
            <OffthreadVideo
              src={staticFile(broll.asset)}
              startFrom={Math.round(broll.startSeconds * FPS)}
              volume={0}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                objectPosition: 'center center',
              }}
            />
          )}
        </AbsoluteFill>
        <Captions
          list={shot.cues.map((cue) => ({
            ...cue,
            s: cue.s - handoffFrame / FPS,
            e: cue.e - handoffFrame / FPS,
          }))}
          evidence
        />
      </Sequence>
      {/* One continuous verified narration track; evidence itself remains muted. */}
      <Audio src={staticFile(shot.clip)} volume={1} />
    </AbsoluteFill>
  );
};

const ShotView: React.FC<{shot: Shot}> = ({shot}) => {
  if (shot.broll) return <EvidenceShot shot={shot} broll={shot.broll} />;
  return <PresenterShot shot={shot} captions />;
};

const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const enter = spring({frame, fps: FPS, config: {damping: 180}});
  const outro = story.outro;
  return (
    <AbsoluteFill
      style={{
        backgroundColor: C.bg,
        fontFamily: UI_FONT,
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
    <AbsoluteFill style={{backgroundColor: C.bg, fontFamily: UI_FONT}}>
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
