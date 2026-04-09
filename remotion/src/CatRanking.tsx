/**
 * CatRanking.tsx — Remotion composition for CatCentral YouTube Shorts.
 *
 * Visual design mirrors the highest-performing cat ranking Shorts (1.5M+ views):
 *
 *   • Full-screen source clip (object-fit: cover → always portrait-fills)
 *   • Bold title bar (black overlay, top)
 *   • Left-side rank list with spring-animated gold reveal (punchy 3× zoom-in)
 *   • 🔥 VIRAL badge on clips that appear in multiple 1M+ view ranking videos
 *   • 3-second countdown (3 … 2 … 1) before the #1 reveal clip
 *   • Outro: all ranks light up gold for 1.5 s at the end of the last clip
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
  /** How many different 1M+ view ranking videos have featured this clip */
  viralScore?: number;
}

export interface CatRankingProps {
  clips: ClipData[];
  title: string;
  watermark: string;
  totalFrames: number;
  hasWoosh: boolean;
  /** Show 3-2-1 countdown overlay at the start of the #1 reveal clip */
  hasCountdown?: boolean;
}

// ─── Design constants ─────────────────────────────────────────────────────────

const GOLD      = '#FFD700';
const WHITE     = '#FFFFFF';
const DIM       = 'rgba(255,255,255,0.50)';
const VERY_DIM  = 'rgba(255,255,255,0.22)';
const RED_BADGE = 'rgba(238,17,17,0.88)';
const FIRE_BG   = 'rgba(255,69,0,0.90)';

const Y_START     = 165;
const Y_END       = 1820;
const TITLE_BAR_H = 118;

/** Frames devoted to the 3-2-1 countdown at the start of the last clip */
const COUNTDOWN_FRAMES = 90; // 3 s @ 30 fps

/** Frames for the all-gold outro at the end of the last clip */
const OUTRO_FRAMES = 45; // 1.5 s @ 30 fps

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

// ─── Countdown overlay (3 … 2 … 1) ───────────────────────────────────────────

const CountdownOverlay: React.FC<{frame: number; fps: number}> = ({frame, fps}) => {
  if (frame >= COUNTDOWN_FRAMES) return null;

  const numIndex  = Math.floor(frame / fps); // 0 → 3, 1 → 2, 2 → 1
  const num       = 3 - numIndex;
  if (num < 1) return null;

  const localFrame = frame % fps;

  // Punch-in from 2.5× then settle to 1×
  const scaleSpring = spring({
    frame: localFrame,
    fps,
    config: {damping: 10, stiffness: 300, mass: 0.4},
    durationInFrames: fps,
  });
  const scale = interpolate(scaleSpring, [0, 1], [2.5, 1.0]);

  const opacity = interpolate(
    localFrame,
    [0, 4, fps - 8, fps - 1],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  // Brief white flash on each number change
  const flashOpacity = interpolate(
    localFrame,
    [0, 3],
    [0.22, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <>
      <div style={{position:'absolute', inset:0, background:'#fff', opacity:flashOpacity, pointerEvents:'none'}} />
      <div style={{position:'absolute', inset:0, background:'rgba(0,0,0,0.55)', opacity}} />
      <div style={{
        position:       'absolute',
        inset:          0,
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
        opacity,
        transform:      `scale(${scale})`,
      }}>
        <span style={{
          color:      GOLD,
          fontSize:   380,
          fontFamily: "'Arial Black', Arial, sans-serif",
          fontWeight: 900,
          textShadow: '0 0 80px rgba(255,215,0,0.8), 8px 8px 0 rgba(0,0,0,0.9)',
          lineHeight: 1,
        }}>
          {num}
        </span>
      </div>
    </>
  );
};

// ─── Single clip view ──────────────────────────────────────────────────────────

const ClipView: React.FC<{
  clip:         ClipData;
  allClips:     ClipData[];
  idx:          number;
  title:        string;
  watermark:    string;
  isFirst:      boolean;
  isLast:       boolean;
  hasWoosh:     boolean;
  hasCountdown: boolean;
}> = ({clip, allClips, idx, title, watermark, isFirst, isLast, hasWoosh, hasCountdown}) => {
  const frame      = useCurrentFrame();
  const {fps}      = useVideoConfig();
  const n          = allClips.length;
  const spacing    = (Y_END - Y_START) / n;
  const totalF     = clip.durationFrames;

  // Rank number: punch in from 3× scale
  const revealSpring = spring({
    frame,
    fps,
    config: {damping: 10, stiffness: 260, mass: 0.45},
    durationInFrames: 28,
  });
  const rankScale = interpolate(revealSpring, [0, 1], [3.0, 1.0]);

  const tLen      = title.length;
  const titleSize = tLen > 30 ? 36 : tLen > 22 ? 44 : 52;

  // Like & Subscribe badge
  const badgeOpacity = isFirst
    ? interpolate(
        frame,
        [3 * fps, 3 * fps + 9, 6 * fps - 9, 6 * fps],
        [0, 1, 1, 0],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      )
    : 0;

  // Outro: all ranks go gold in last OUTRO_FRAMES
  const outroProgress = isLast
    ? interpolate(
        frame,
        [totalF - OUTRO_FRAMES, totalF - OUTRO_FRAMES + 8, totalF],
        [0, 1, 1],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      )
    : 0;

  // Viral badge fade-in
  const isViral   = (clip.viralScore ?? 0) >= 2;
  const viralFade = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill>

      {/* ── Background video ─────────────────────────────────────────────── */}
      <OffthreadVideo
        src={staticFile(`clips/${clip.path}`)}
        style={{width:'100%', height:'100%', objectFit:'cover', objectPosition:'center'}}
      />

      {/* ── Whoosh SFX ───────────────────────────────────────────────────── */}
      {hasWoosh && (
        <Audio src={staticFile('sfx/woosh.mp3')} volume={2} startFrom={0} endAt={Math.round(0.7 * fps)} />
      )}

      {/* ── Title bar ────────────────────────────────────────────────────── */}
      <div style={{
        position:'absolute', top:0, left:0, right:0,
        height:TITLE_BAR_H,
        background:'rgba(0,0,0,0.78)',
        display:'flex', alignItems:'center', justifyContent:'center',
        padding:'0 20px',
      }}>
        <span style={{
          color:WHITE, fontSize:titleSize,
          fontFamily:"'Arial Black', Arial, sans-serif",
          fontWeight:900, textAlign:'center',
          textShadow:'3px 3px 8px rgba(0,0,0,0.95)',
          letterSpacing:'-0.5px',
        }}>
          {title.toUpperCase()}
        </span>
      </div>

      {/* ── Rank list ────────────────────────────────────────────────────── */}
      {allClips.map((c, i) => {
        const isCur  = i === idx;
        const isPast = i <  idx;
        const y      = Y_START + i * spacing;

        // During outro all entries go gold
        const goldColor = outroProgress > 0.05 ? GOLD : (isCur ? GOLD : isPast ? DIM : VERY_DIM);
        const lblColor  = outroProgress > 0.05 ? WHITE : (isCur ? WHITE : isPast ? DIM : VERY_DIM);

        const numSize  = isCur ? 112 : 76;
        const lblSize  = isCur ? 56  : 40;
        const numScl   = isCur ? rankScale : 1;
        const lblOp    = isCur
          ? interpolate(frame, [0, 18], [0, 1], {extrapolateLeft:'clamp', extrapolateRight:'clamp'})
          : 1;

        return (
          <React.Fragment key={i}>
            <div style={{
              position:'absolute', left:16, top:y,
              fontSize:numSize, color:goldColor,
              fontFamily:"'Arial Black', Arial, sans-serif",
              fontWeight:900, textShadow:'4px 4px 12px rgba(0,0,0,0.95)',
              transform:`scale(${numScl})`, transformOrigin:'left center',
              lineHeight:1,
            }}>
              {n - i}.
            </div>
            <div style={{
              position:'absolute', left:108,
              top:y + Math.max(0, (numSize - lblSize) / 2 + 4),
              fontSize:lblSize, color:lblColor,
              fontFamily:"'Arial Black', Arial, sans-serif",
              fontWeight:900, textShadow:'3px 3px 8px rgba(0,0,0,0.9)',
              opacity:lblOp,
            }}>
              {isCur || isPast ? c.label : '?'}
            </div>
          </React.Fragment>
        );
      })}

      {/* ── 🔥 VIRAL badge (proven cross-channel clip) ───────────────────── */}
      {isViral && (
        <div style={{
          position:'absolute', right:18, top:TITLE_BAR_H + 16,
          background:FIRE_BG, borderRadius:28,
          paddingLeft:18, paddingRight:18, paddingTop:8, paddingBottom:8,
          display:'flex', alignItems:'center', gap:6,
          opacity:viralFade,
          transform:`scale(${interpolate(viralFade,[0,1],[0.6,1])})`,
          transformOrigin:'top right',
        }}>
          <span style={{fontSize:36}}>🔥</span>
          <span style={{
            color:WHITE, fontSize:32,
            fontFamily:"'Arial Black', Arial, sans-serif",
            fontWeight:900, textShadow:'1px 1px 4px rgba(0,0,0,0.8)',
          }}>
            VIRAL
          </span>
        </div>
      )}

      {/* ── Like & Subscribe badge (first clip only) ─────────────────────── */}
      {badgeOpacity > 0 && (
        <div style={{
          position:'absolute', left:280, top:1710,
          width:520, height:88,
          background:RED_BADGE, borderRadius:44,
          display:'flex', flexDirection:'column',
          alignItems:'center', justifyContent:'center',
          opacity:badgeOpacity,
        }}>
          <span style={{
            color:WHITE, fontSize:40,
            fontFamily:"'Arial Black', Arial, sans-serif",
            fontWeight:900, textShadow:'2px 2px 4px rgba(0,0,0,0.8)',
          }}>
            LIKE &amp; SUBSCRIBE
          </span>
          <span style={{color:'rgba(255,255,255,0.85)', fontSize:24, fontFamily:'Arial, sans-serif', marginTop:2}}>
            for more cat videos
          </span>
        </div>
      )}

      {/* ── Moving watermark ─────────────────────────────────────────────── */}
      <div style={wmStyle(frame, fps)}>{watermark}</div>

      {/* ── 3-2-1 Countdown (last clip only) ─────────────────────────────── */}
      {isLast && hasCountdown && <CountdownOverlay frame={frame} fps={fps} />}

    </AbsoluteFill>
  );
};

// ─── Root composition ──────────────────────────────────────────────────────────

export const CatRanking: React.FC<CatRankingProps> = ({
  clips, title, watermark, hasWoosh, hasCountdown = false,
}) => {
  let offset = 0;
  return (
    <AbsoluteFill style={{background:'#000'}}>
      {clips.map((clip, idx) => {
        const from   = offset;
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
              isLast={idx === clips.length - 1}
              hasWoosh={hasWoosh}
              hasCountdown={hasCountdown}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
