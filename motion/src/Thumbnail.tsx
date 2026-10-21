import { AbsoluteFill, Img, staticFile } from "remotion";
import { colors, fonts } from "./theme";
import type { ThumbnailProps } from "./types";

const fill: React.CSSProperties = { width: "100%", height: "100%", objectFit: "cover" };

// miniatura do YouTube (1280x720): a imagem do tema escurecida e o título centralizado por cima
export const Thumbnail: React.FC<ThumbnailProps> = ({ title, image }) => {
  // títulos longos encolhem para caber em até ~3 linhas
  const size = Math.round(150 * Math.min(1, Math.sqrt(22 / Math.max(22, title.length))));
  const src = image ? staticFile(image.src) : null;
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0b0f" }}>
      {src && image!.aspect >= 1.2 && <Img src={src} style={fill} />}
      {/* foto alta (retrato): inteira, sobre ela mesma desfocada */}
      {src && image!.aspect < 1.2 && (
        <AbsoluteFill>
          <Img src={src} style={{ ...fill, filter: "blur(30px) brightness(0.6)", transform: "scale(1.2)" }} />
          <Img src={src} style={{ width: "100%", height: "100%", objectFit: "contain" }} />
        </AbsoluteFill>
      )}
      {!src && <AbsoluteFill style={{ background: "linear-gradient(135deg, #101522 0%, #24202e 55%, #3b2a1a 100%)" }} />}
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, rgba(0,0,0,0.35) 30%, rgba(0,0,0,0.75) 100%)" }} />
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", textAlign: "center", padding: "0 8%", color: colors.text }}>
        <div style={{ fontFamily: fonts.title, fontSize: size, lineHeight: 0.95, letterSpacing: "0.02em", textShadow: "0 4px 0 rgba(0,0,0,0.5), 0 8px 40px rgba(0,0,0,0.8)" }}>
          {title}
        </div>
        <div style={{ height: 8, width: "18%", backgroundColor: colors.accent, marginTop: 24, borderRadius: 4 }} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
