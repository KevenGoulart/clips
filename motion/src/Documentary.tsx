import { AbsoluteFill, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Background } from "./scenes/Background";
import { Captions } from "./scenes/Captions";
import { DateCard, ListCard, LowerThird, QuoteCard, StatCard, TitleCard } from "./scenes/Overlays";
import { FADE } from "./theme";
import type { Scene, Timeline } from "./types";

// quanto o fundo escurece em cada tipo de cena para o texto ficar legível
const DIM: Record<Scene["kind"], number> = { image: 0, clip: 0, title: 0.5, date: 0.45, quote: 0.6, stat: 0.55, list: 0.6 };

const SceneView: React.FC<{ scene: Scene; index: number }> = ({ scene, index }) => {
  const frame = useCurrentFrame();
  const opacity = index === 0 ? 1 : interpolate(frame, [0, FADE], [0, 1], { extrapolateRight: "clamp" });
  const d = scene.duration;

  // vídeo pronto (ex.: exportado do Claude Motion): ocupa a tela inteira, sem textos por cima
  if (scene.kind === "clip" && scene.shots[0]?.media) {
    return (
      <AbsoluteFill style={{ opacity, backgroundColor: "#000" }}>
        <OffthreadVideo src={staticFile(scene.shots[0].media.src)} muted style={{ width: "100%", height: "100%", objectFit: "contain" }} />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ opacity }}>
      <Background shots={scene.shots} dim={DIM[scene.kind]} />
      {scene.kind === "title" && <TitleCard text={scene.text ?? ""} subtext={scene.subtext} duration={d} />}
      {scene.kind === "date" && <DateCard text={scene.text ?? ""} subtext={scene.subtext} duration={d} />}
      {scene.kind === "quote" && <QuoteCard text={scene.text ?? ""} subtext={scene.subtext} duration={d} />}
      {scene.kind === "stat" && <StatCard text={scene.text ?? ""} subtext={scene.subtext} duration={d} />}
      {scene.kind === "list" && <ListCard text={scene.text} items={scene.items ?? []} duration={d} />}
      {scene.kind === "image" && scene.label && <LowerThird text={scene.label} duration={d} />}
    </AbsoluteFill>
  );
};

export const Documentary: React.FC<Timeline> = ({ scenes, captions, showCaptions, durationInFrames }) => {
  const frame = useCurrentFrame();
  // escurece no último segundo
  const outro = interpolate(frame, [durationInFrames - 30, durationInFrames], [0, 1], { extrapolateLeft: "clamp" });
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {scenes.map((scene, i) => (
        // cada cena dura FADE frames a mais e a seguinte aparece por cima dela (dissolve)
        <Sequence key={i} from={scene.from} durationInFrames={scene.duration + (i === scenes.length - 1 ? 0 : FADE)}>
          <SceneView scene={scene} index={i} />
        </Sequence>
      ))}
      {showCaptions && <Captions captions={captions} />}
      <AbsoluteFill style={{ backgroundColor: "#000", opacity: outro }} />
    </AbsoluteFill>
  );
};
