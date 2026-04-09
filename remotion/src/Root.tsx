import React from 'react';
import {Composition} from 'remotion';
import {CatRanking, CatRankingProps} from './CatRanking';

const defaultProps: CatRankingProps = {
  clips: [
    {path: 'placeholder.mp4', rank: 5, label: 'CAT CLIP', durationFrames: 750},
    {path: 'placeholder.mp4', rank: 4, label: 'CAT CLIP', durationFrames: 750},
    {path: 'placeholder.mp4', rank: 3, label: 'CAT CLIP', durationFrames: 750},
    {path: 'placeholder.mp4', rank: 2, label: 'CAT CLIP', durationFrames: 750},
    {path: 'placeholder.mp4', rank: 1, label: 'CAT CLIP', durationFrames: 750},
  ],
  title: 'Funniest Cat Moments Ranked',
  watermark: '@CatCentral',
  totalFrames: 3750,
  hasWoosh: false,
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CatRanking"
      component={CatRanking}
      durationInFrames={3750}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => ({
        durationInFrames: props.totalFrames || 3750,
      })}
    />
  );
};
