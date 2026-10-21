import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import { colors, fonts } from "../theme";
import type { Caption } from "../types";

const Line: React.FC<{ caption: Caption }> = ({ caption }) => {
  const frame = useCurrentFrame() + caption.start; // frame absoluto
  const { width, height } = useVideoConfig();
  const vertical = height > width;
  const size = Math.round((vertical ? 64 : 46) * (Math.min(width, height * (16 / 9)) / 1920) * (vertical ? 1.6 : 1));
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: vertical ? "28%" : "6%" }}>
      <div style={{ maxWidth: "82%", textAlign: "center", fontFamily: fonts.body, fontWeight: 800, fontSize: size, lineHeight: 1.3, padding: "8px 22px", borderRadius: 10, backgroundColor: "rgba(0,0,0,0.45)" }}>
        {caption.words.map((w, i) => (
          // a palavra sendo falada fica dourada
          <span key={i} style={{ color: frame >= w.start && (i === caption.words.length - 1 || frame < caption.words[i + 1].start) ? colors.accent : colors.text }}>
            {w.text}{" "}
          </span>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const Captions: React.FC<{ captions: Caption[] }> = ({ captions }) => (
  <>
    {captions.map((c, i) => (
      <Sequence key={i} from={c.start} durationInFrames={Math.max(1, c.end - c.start)} layout="none">
        <Line caption={c} />
      </Sequence>
    ))}
  </>
);
