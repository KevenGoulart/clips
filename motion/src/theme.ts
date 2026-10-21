import { loadFont as loadBebas } from "@remotion/google-fonts/BebasNeue";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadPlayfair } from "@remotion/google-fonts/PlayfairDisplay";

export const fonts = {
  title: loadBebas("normal", { weights: ["400"], subsets: ["latin", "latin-ext"] }).fontFamily,
  body: loadInter("normal", { weights: ["500", "800"], subsets: ["latin", "latin-ext"] }).fontFamily,
  quote: loadPlayfair("italic", { weights: ["500"], subsets: ["latin", "latin-ext"] }).fontFamily,
};

export const colors = {
  accent: "#f2b134", // dourado de documentário
  text: "#ffffff",
  dim: "rgba(255,255,255,0.75)",
  shade: "rgba(8,8,12,0.62)",
};

// sobreposição (em frames) entre uma cena e a próxima: a nova cena aparece por cima da anterior
export const FADE = 12;

// número estável a partir de um texto: cada imagem ganha sempre o mesmo movimento de câmera
export const hash = (s: string) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};
