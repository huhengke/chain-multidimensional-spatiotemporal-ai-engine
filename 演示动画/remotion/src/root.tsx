import { Composition } from 'remotion';
import { WisdomTreeQueryExplainer } from './wisdom-tree-query-explainer';

export const RemotionRoot = () => (
  <Composition
    id="wisdom-tree-query-explainer"
    component={WisdomTreeQueryExplainer}
    durationInFrames={960}
    fps={30}
    width={1280}
    height={720}
  />
);
