import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { colors, fonts } from "../theme";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

// entrada com mola (0 -> 1) atrasada em `delay` frames
const useEnter = (delay = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping: 200 } });
};

// some suavemente no fim da cena
const useExit = (duration: number) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [duration - 12, duration], [1, 0], clamp);
};

// tamanho da fonte encolhe para textos longos (base pensada para 1920 px de largura)
const useFit = () => {
  const { width, height } = useVideoConfig();
  const scale = Math.min(width, height * (16 / 9)) / 1920;
  return (text: string, base: number, chars: number) =>
    Math.round(base * scale * Math.min(1, Math.sqrt(chars / Math.max(chars, text.length))));
};

const center: React.CSSProperties = {
  justifyContent: "center",
  alignItems: "center",
  textAlign: "center",
  padding: "0 8%",
  color: colors.text,
};

export const TitleCard: React.FC<{ text: string; subtext?: string; duration: number }> = ({ text, subtext, duration }) => {
  const fit = useFit();
  const enter = useEnter(4);
  const line = useEnter(14);
  const exit = useExit(duration);
  return (
    <AbsoluteFill style={{ ...center, opacity: exit }}>
      {subtext && (
        <div style={{ fontFamily: fonts.body, fontWeight: 800, fontSize: fit(subtext, 34, 30), letterSpacing: "0.35em", textTransform: "uppercase", color: colors.accent, opacity: enter, marginBottom: 18 }}>
          {subtext}
        </div>
      )}
      <div style={{ fontFamily: fonts.title, fontSize: fit(text, 170, 22), lineHeight: 1, letterSpacing: "0.02em", textShadow: "0 6px 40px rgba(0,0,0,0.6)", opacity: enter, transform: `translateY(${(1 - enter) * 40}px)` }}>
        {text}
      </div>
      <div style={{ height: 6, width: `${line * 22}%`, backgroundColor: colors.accent, marginTop: 28, borderRadius: 3 }} />
    </AbsoluteFill>
  );
};

export const DateCard: React.FC<{ text: string; subtext?: string; duration: number }> = ({ text, subtext, duration }) => {
  const fit = useFit();
  const frame = useCurrentFrame();
  const enter = useEnter(2);
  const sub = useEnter(16);
  const exit = useExit(duration);
  const spacing = interpolate(frame, [0, duration], [0.04, 0.12]);
  return (
    <AbsoluteFill style={{ ...center, opacity: exit }}>
      <div style={{ fontFamily: fonts.title, fontSize: fit(text, 300, 10), lineHeight: 1, letterSpacing: `${spacing}em`, textShadow: "0 8px 60px rgba(0,0,0,0.7)", opacity: enter, transform: `scale(${0.85 + 0.15 * enter})` }}>
        {text}
      </div>
      {subtext && (
        <div style={{ fontFamily: fonts.body, fontWeight: 500, fontSize: fit(subtext, 46, 40), color: colors.dim, marginTop: 10, opacity: sub, transform: `translateY(${(1 - sub) * 20}px)` }}>
          {subtext}
        </div>
      )}
    </AbsoluteFill>
  );
};

export const QuoteCard: React.FC<{ text: string; subtext?: string; duration: number }> = ({ text, subtext, duration }) => {
  const fit = useFit();
  const frame = useCurrentFrame();
  const exit = useExit(duration);
  const words = text.split(/\s+/);
  // as palavras aparecem uma a uma ao longo do primeiro terço da cena
  const perWord = Math.max(1, Math.min(6, (duration * 0.35) / words.length));
  const author = useEnter(Math.round(words.length * perWord) + 6);
  return (
    <AbsoluteFill style={{ ...center, opacity: exit }}>
      <div style={{ fontFamily: fonts.quote, fontStyle: "italic", fontSize: fit(text, 84, 70), lineHeight: 1.25, maxWidth: "80%", textShadow: "0 4px 30px rgba(0,0,0,0.7)" }}>
        <span style={{ color: colors.accent }}>“</span>
        {words.map((w, i) => (
          <span key={i} style={{ opacity: interpolate(frame, [i * perWord, i * perWord + 8], [0, 1], clamp) }}>
            {i > 0 ? " " : ""}{w}
          </span>
        ))}
        <span style={{ color: colors.accent }}>”</span>
      </div>
      {subtext && (
        <div style={{ fontFamily: fonts.body, fontWeight: 800, fontSize: fit(subtext, 38, 30), letterSpacing: "0.2em", textTransform: "uppercase", color: colors.accent, marginTop: 36, opacity: author }}>
          — {subtext}
        </div>
      )}
    </AbsoluteFill>
  );
};

// conta de 0 até o número do texto, mantendo o resto ("70 milhões", "US$ 3,5 bi", "1.500 km")
const countUp = (text: string, t: number) => {
  const m = text.match(/\d[\d.,]*/);
  if (!m) return text;
  const raw = m[0].replace(/[.,]$/, "");
  const decimals = raw.includes(",") ? raw.split(",")[1].length : 0;
  const value = Number(raw.replace(/\./g, "").replace(",", "."));
  if (!Number.isFinite(value)) return text;
  // anos e números pequenos sem separador de milhar ("1822", não "1.822")
  const grouping = raw.includes(".") || value >= 10000;
  const shown = (value * t).toLocaleString("pt-BR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: grouping });
  return text.replace(raw, shown);
};

export const StatCard: React.FC<{ text: string; subtext?: string; duration: number }> = ({ text, subtext, duration }) => {
  const fit = useFit();
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = useEnter(0);
  const exit = useExit(duration);
  const t = interpolate(frame, [4, 4 + fps * 1.6], [0, 1], { ...clamp, easing: (x) => 1 - Math.pow(1 - x, 3) });
  const sub = useEnter(Math.round(fps * 1.2));
  return (
    <AbsoluteFill style={{ ...center, opacity: exit }}>
      <div style={{ fontFamily: fonts.title, fontSize: fit(text, 260, 12), lineHeight: 1, color: colors.accent, textShadow: "0 8px 60px rgba(0,0,0,0.7)", opacity: enter }}>
        {countUp(text, t)}
      </div>
      {subtext && (
        <div style={{ fontFamily: fonts.body, fontWeight: 800, fontSize: fit(subtext, 52, 40), marginTop: 12, opacity: sub, transform: `translateY(${(1 - sub) * 20}px)` }}>
          {subtext}
        </div>
      )}
    </AbsoluteFill>
  );
};

export const ListCard: React.FC<{ text?: string; items: string[]; duration: number }> = ({ text, items, duration }) => {
  const fit = useFit();
  const { fps } = useVideoConfig();
  const head = useEnter(0);
  const exit = useExit(duration);
  // os itens entram distribuídos ao longo da cena, acompanhando a narração
  const step = Math.max(8, Math.min(fps * 2.5, (duration * 0.7) / Math.max(1, items.length)));
  return (
    <AbsoluteFill style={{ justifyContent: "center", padding: "0 12%", color: colors.text, opacity: exit }}>
      {text && (
        <div style={{ fontFamily: fonts.title, fontSize: fit(text, 110, 26), color: colors.accent, marginBottom: 30, opacity: head }}>
          {text}
        </div>
      )}
      {items.map((item, i) => (
        <ListItem key={i} text={item} delay={Math.round(10 + i * step)} />
      ))}
    </AbsoluteFill>
  );
};

const ListItem: React.FC<{ text: string; delay: number }> = ({ text, delay }) => {
  const fit = useFit();
  const enter = useEnter(delay);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 28, marginBottom: 22, opacity: enter, transform: `translateX(${(1 - enter) * -60}px)` }}>
      <div style={{ width: 18, height: 18, flexShrink: 0, backgroundColor: colors.accent, transform: "rotate(45deg)" }} />
      <div style={{ fontFamily: fonts.body, fontWeight: 800, fontSize: fit(text, 58, 40), textShadow: "0 3px 20px rgba(0,0,0,0.7)" }}>{text}</div>
    </div>
  );
};

// tarja no canto superior esquerdo: nome de lugar, pessoa ou ano (some depois de ~5 s)
export const LowerThird: React.FC<{ text: string; duration: number }> = ({ text, duration }) => {
  const fit = useFit();
  const enter = useEnter(10);
  const exit = useExit(Math.min(duration, 150));
  const { height } = useVideoConfig();
  return (
    <AbsoluteFill style={{ justifyContent: "flex-start", alignItems: "flex-start", padding: `${Math.round(height * 0.07)}px 5%` }}>
      <div style={{ display: "flex", alignItems: "stretch", opacity: exit, transform: `translateX(${(1 - enter) * -40}px)`, clipPath: `inset(0 ${(1 - enter) * 100}% 0 0)` }}>
        <div style={{ width: 8, backgroundColor: colors.accent }} />
        <div style={{ fontFamily: fonts.body, fontWeight: 800, fontSize: fit(text, 40, 40), letterSpacing: "0.08em", textTransform: "uppercase", color: colors.text, backgroundColor: colors.shade, padding: "14px 26px" }}>
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};
