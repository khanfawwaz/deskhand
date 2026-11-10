import React from 'react';
import { Composition } from 'remotion';
import { Video } from './Walkthrough';
import github from './scenes-github.json';
import store from './scenes-store.json';
import { Shot1, Shot2, Shot3, Shot4, Shot5, Tile, Marquee, Thumb } from './StoreAssets';

const total = (d: { scenes: { frames: number }[] }) => d.scenes.reduce((a, s) => a + s.frames, 0);

export const Root: React.FC = () => (
  <>
    <Composition id="Github" component={() => <Video scenes={github.scenes as never} />} width={1920} height={1080} fps={github.fps} durationInFrames={total(github)} />
    <Composition id="ChromeReview" component={() => <Video scenes={store.scenes as never} />} width={1920} height={1080} fps={store.fps} durationInFrames={total(store)} />
    <Composition id="Store1" component={Shot1} width={1280} height={800} fps={30} durationInFrames={1} />
    <Composition id="Store2" component={Shot2} width={1280} height={800} fps={30} durationInFrames={1} />
    <Composition id="Store3" component={Shot3} width={1280} height={800} fps={30} durationInFrames={1} />
    <Composition id="Store4" component={Shot4} width={1280} height={800} fps={30} durationInFrames={1} />
    <Composition id="Store5" component={Shot5} width={1280} height={800} fps={30} durationInFrames={1} />
    <Composition id="StoreTile" component={Tile} width={440} height={280} fps={30} durationInFrames={1} />
    <Composition id="StoreMarquee" component={Marquee} width={1400} height={560} fps={30} durationInFrames={1} />
    <Composition id="Thumb" component={Thumb} width={1280} height={720} fps={30} durationInFrames={1} />
  </>
);
