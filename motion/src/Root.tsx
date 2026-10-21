import { Composition } from "remotion";
import { Documentary } from "./Documentary";
import { Thumbnail } from "./Thumbnail";
import type { ThumbnailProps, Timeline } from "./types";

// usado só no Remotion Studio (npm run studio); o render real recebe o timeline.json do projeto
const preview: Timeline = {
  fps: 30,
  width: 1920,
  height: 1080,
  durationInFrames: 30 * 24,
  title: "Prévia",
  showCaptions: true,
  scenes: [
    { from: 0, duration: 120, kind: "title", text: "A Queda de Roma", subtext: "Capítulo 1", shots: [{ from: 0, duration: 120, media: null }] },
    { from: 120, duration: 150, kind: "date", text: "476 d.C.", subtext: "Fim do Império do Ocidente", shots: [{ from: 0, duration: 150, media: null }] },
    { from: 270, duration: 150, kind: "quote", text: "Vim, vi e venci.", subtext: "Júlio César", shots: [{ from: 0, duration: 150, media: null }] },
    { from: 420, duration: 150, kind: "stat", text: "70 milhões", subtext: "de habitantes no auge do império", shots: [{ from: 0, duration: 150, media: null }] },
    { from: 570, duration: 150, kind: "list", text: "Causas da queda", items: ["Crise econômica", "Invasões bárbaras", "Divisão do império"], shots: [{ from: 0, duration: 150, media: null }] },
  ],
  captions: [
    { start: 0, end: 90, words: [{ start: 0, text: "Durante" }, { start: 20, text: "séculos," }, { start: 45, text: "Roma" }, { start: 60, text: "dominou" }, { start: 75, text: "tudo." }] },
  ],
};

const previewThumb: ThumbnailProps = { title: "A Queda de Roma", image: null };

export const Root = () => (
  <>
    <Composition
      id="Documentario"
      component={Documentary}
      defaultProps={preview}
      durationInFrames={preview.durationInFrames}
      fps={preview.fps}
      width={preview.width}
      height={preview.height}
      calculateMetadata={({ props }) => ({
        durationInFrames: props.durationInFrames,
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
    <Composition id="Miniatura" component={Thumbnail} defaultProps={previewThumb} durationInFrames={1} fps={30} width={1280} height={720} />
  </>
);
