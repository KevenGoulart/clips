import { AbsoluteFill, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { FADE, hash } from "../theme";
import type { Media, Shot } from "../types";

// movimento de câmera lento sobre a foto (efeito Ken Burns); a direção depende da imagem
const kenBurns = (seed: number, t: number, amount = 1) => {
  const zoom = seed % 2 === 0 ? t : 1 - t;
  const scale = 1 + amount * (0.04 + 0.14 * zoom);
  const pan = [0, 1, -1][seed % 3] * (t - 0.5) * 4 * amount; // % do quadro
  const tilt = [0, -1, 1][Math.floor(seed / 3) % 3] * (t - 0.5) * 2.5 * amount;
  return `scale(${scale}) translate(${pan}%, ${tilt}%)`;
};

const fill: React.CSSProperties = { width: "100%", height: "100%", objectFit: "cover" };

const MediaView: React.FC<{ media: Media; duration: number }> = ({ media, duration }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const src = staticFile(media.src);
  const t = Math.min(1, frame / Math.max(1, duration));

  if (media.type === "video") {
    return <OffthreadVideo src={src} muted style={fill} />;
  }

  const seed = hash(media.src);
  // foto muito mais alta/larga que o quadro (retratos, panoramas, mapas): inteira, sobre um fundo desfocado
  const ratio = media.aspect / (width / height);
  if (ratio < 0.8 || ratio > 1.6) {
    return (
      <AbsoluteFill>
        <Img src={src} style={{ ...fill, filter: "blur(40px) brightness(0.45)", transform: "scale(1.2)" }} />
        <AbsoluteFill style={{ transform: kenBurns(seed, t, 0.4) }}>
          <Img src={src} style={{ width: "100%", height: "100%", objectFit: "contain" }} />
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  return <Img src={src} style={{ ...fill, transform: kenBurns(seed, t) }} />;
};

// fundo sem mídia: gradiente escuro que se move devagar
const Placeholder: React.FC = () => {
  const frame = useCurrentFrame();
  const angle = interpolate(frame, [0, 600], [120, 160]);
  return <AbsoluteFill style={{ background: `linear-gradient(${angle}deg, #101522 0%, #24202e 55%, #3b2a1a 100%)` }} />;
};

export const Background: React.FC<{ shots: Shot[]; dim?: number }> = ({ shots, dim = 0 }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0b0f" }}>
      {shots.map((shot, i) => {
        const last = i === shots.length - 1;
        // cada tomada entra em fade sobre a anterior
        const fadeIn = i === 0 ? 1 : interpolate(frame - shot.from, [0, FADE], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return (
          <Sequence key={i} from={shot.from} durationInFrames={shot.duration + (last ? FADE : FADE + 1)} layout="none">
            <AbsoluteFill style={{ opacity: fadeIn }}>
              {shot.media ? <MediaView media={shot.media} duration={shot.duration + FADE} /> : <Placeholder />}
            </AbsoluteFill>
          </Sequence>
        );
      })}
      {/* vinheta e escurecimento para o texto se destacar */}
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.55) 100%)" }} />
      {dim > 0 && <AbsoluteFill style={{ backgroundColor: `rgba(6,6,10,${dim})` }} />}
    </AbsoluteFill>
  );
};
