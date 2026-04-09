/**
 * CatRanking.tsx — Remotion composition for CatCentral YouTube Shorts.
 *
 * Renders a ranking-style video:
 *   • Full-screen source clip (object-fit: cover → always portrait-fills)
 *   • Title bar (black overlay, top)
 *   • Left-side rank list (spring-animated reveal, gold current rank)
 *   • Moving watermark (corner rotation every 12 s)
 *   • Like & Subscribe badge (first clip, 3–6 s, fade in/out)
 *   • Woosh SFX at the start of every clip (if available)
 */

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
  useVideoConfig,
} from 'remotion';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ClipData {
  /** Path relative to remotion/public/clips/ */
  path: string;
  rank: number;
  /** Short 2-word ALL-CAPS sidebar label */
  label: string;
  durationFrames: number;
}

export interface CatRankingProps {
  clips: ClipData[];
  title: string;
  watermark: string;
  totalFrames: number;
  hasWoosh: boolean;
}

// ─── Design constants ─────────────────────────────────────────────────────────

const GOLD     = '#FFD700';
const WHITE    = '#FFFFFF';
const DIM      = 'rgba(255,255,255,0.50)';
const VERY_DIM = 'rgba(255,255,255,0.22)';

/** Y position of first rank item (below title bar) */
const Y_START      = 165;
/** Y position of last rank item */
const Y_END        = 1820;
const TITLE_BAR_H  = 118;

// ─── Watermark position ────────────────────────────────────────────────────────

function wmStyle(frame: number, fps: number): React.CSSProperties {
  const cycle = Math.floor(frame / (12 * fps)) % 4;
  const pad   = 55;
  const corners: React.CSSProperties[] = [
    {top: pad + 20, left:  pad},
    {top: pad + 20, right: pad},
    {bottom: pad,   left:  pad},
    {bottom: pad,   right: pad},
  ];
  return {
    position:   'absolute',
    ...corners[cycle],
    color:      'rgba(255,255,255,0.75)',
    fontSize:   34,
    fontFamily: "'Arial Black', Arial, sans-serif",
    fontWeight: 900,
    textShadow: '1px 1px 5px rgba(0,0,0,0.7)',
    lineHeight: 1,
  };
}

// ─── Single clip view ──────────────────────────────────────────────────────────

const ClipView: React.FC<{
  clip:      ClipData;
  allClips:  ClipData[];
  idx:       number;
  title:     string;
  watermark: string;
  isFirst:   boolean;
  hasWoosh:  boolean;
}> = ({clip, allClips, idx, title, watermark, isFirst, hasWoosh}) => {
  const frame       = useCurrentFrame();
  const {fps}       = useVideoConfig();
  const n           = allClips.length;
  const spacing     = (Y_END - Y_START) / n;

  // Spring for rank-number entrance
  const rankReveal = spring({
    frame,
    fps,
    config:           {damping: 12, stiffness: 200, mass: 0.55},
    durationInFrames: 25,
  });

  // Title font size (shrinks for long titles)
  const tLen     = title.length;
  const titleSize = tLen > 30 ? 36 : tLen > 22 ? 44 : 52;

  // Like & Subscribe badge opacity (first clip, 3–6 s)
  const badgeOpacity = isFirst
    ? interpolate(
        frame,
        [3 * fps, 3 * fps + 9, 6 * fps - 9, 6 * fps],
        [0, 1, 1, 0],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      )
    : 0;

  return (
    <AbsoluteFill>

      {/* ── Background video (object-fit: cover → always portrait-fills) ── */}
      <OffthreadVideo
        src={staticFile(`clips/${clip.path}`)}
        style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center'}}
      />

      {/* ── Whoosh SFX on clip entry ────────────────────────────────────── */}
      {hasWoosh && (
        <Audio
          src={staticFile('sfx/woosh.mp3')}
          volume={2}
          startFrom={0}
          endAt={Math.round(0.7 * fps)}
        />
      )}

      {/* ── Title bar ──────────────────────────────────────────────────── */}
      <div style={{
        position:       'absolute',
        top: 0, left: 0, right: 0,
        height:         TITLE_BAR_H,
        background:     'rgba(0,0,0,0.78)',
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
        padding:        '0 20px',
      }}>
        <span style={{
          color:       WHITE,
          fontSize:    titleSize,
          fontFamily:  "'Arial Black', Arial, sans-serif",
          fontWeight:  900,
          textAlign:   'center',
          textShadow:  '3px 3px 8px rgba(0,0,0,0.95)',
          letterSpacing: '-0.5px',
        }}>
          {title.toUpperCase()}
        </span>
      </div>

      {/* ── Rank list ──────────────────────────────────────────────────── */}
      {allClips.map((c, i) => {
        const isCurrent = i === idx;
        const isPast    = i <  idx;
        const y         = Y_START + i * spacing;

        const numSize   = isCurrent ? 112 : 76;
        const lblSize   = isCurrent ? 56  : 40;
        const numColor  = isCurrent ? GOLD     : isPast ? DIM     : VERY_DIM;
        const lblColor  = isCurrent ? WHITE    : isPast ? DIM     : VERY_DIM;
        const numScale  = isCurrent ? rankReveal : 1;
        const lblOpacity = isCurrent
          ? interpolate(frame, [0, 18], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
          : 1;

        return (
          <React.Fragment key={i}>

            {/* Rank number */}
            <div style={{
              position:       'absolute',
              left:           16,
              top:            y,
              fontSize:       numSize,
              color:          numColor,
              fontFamily:     "'Arial Black', Arial, sans-serif",
              fontWeight:     900,
              textShadow:     '4px 4px 12px rgba(0,0,0,0.95)',
              transform:      `scale(${numScale})`,
              transformOrigin:'left center',
              lineHeight:     1,
            }}>
              {n - i}.
            </div>

            {/* Sidebar label */}
            <div style={{
              position:   'absolute',
              left:       108,
              top:        y + Math.max(0, (numSize - lblSize) / 2 + 4),
              fontSize:   lblSize,
              color:      lblColor,
              fontFamily: "'Arial Black', Arial, sans-serif",
              fontWeight: 900,
              textShadow: '3px 3px 8px rgba(0,0,0,0.9)',
              opacity:    lblOpacity,
            }}>
              {isCurrent || isPast ? c.label : '?'}
            </div>

          </React.Fragment>
        );
      })}

      {/* ── Like & Subscribe badge ──────────────────────────────────────── */}
      {badgeOpacity > 0 && (
        <div style={{
          position:       'absolute',
          left:           280,
          top:            1710,
          width:          520,
          height:         88,
          background:     'rgba(238,17,17,0.88)',
          borderRadius:   44,
          display:        'flex',
          flexDirection:  'column',
          alignItems:     'center',
          justifyContent: 'center',
          opacity:        badgeOpacity,
        }}>
          <span style={{
            color:      WHITE,
            fontSize:   40,
            fontFamily: "'Arial Black', Arial, sans-serif",
            fontWeight: 900,
            textShadow: '2px 2px 4px rgba(0,0,0,0.8)',
          }}>
            LIKE &amp; SUBSCRIBE
          </span>
          <span style={{
            color:      'rgba(255,255,255,0.85)',
            fontSize:   24,
            fontFamily: 'Arial, sans-serif',
            marginTop:  2,
          }}>
            for more cat videos
          </span>
        </div>
      )}

      {/* ── Moving watermark ───────────────────────────────────────────── */}
      <div style={wmStyle(frame, fps)}>{watermark}</div>

    </AbsoluteFill>
  );
};

// ─── Root composition ──────────────────────────────────────────────────────────

export const CatRanking: React.FC<CatRankingProps> = ({clips, title, watermark, hasWoosh}) => {
  let offset = 0;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {clips.map((clip, idx) => {
        const from = offset;
        offset += clip.durationFrames;
        return (
          <Sequence key={idx} from={from} durationInFrames={clip.durationFrames}>
            <ClipView
              clip={clip}
              allClips={clips}
              idx={idx}
              title={title}
              watermark={watermark}
              isFirst={idx === 0}
              hasWoosh={hasWoosh}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
