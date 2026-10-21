// Formato do timeline.json que o Python (app/render.py) gera para cada vídeo.

export type Media = {
  src: string; // caminho relativo à pasta public do projeto
  type: "image" | "video";
  aspect: number; // largura / altura
  credit?: string;
};

export type Shot = {
  from: number; // frame inicial, relativo à cena
  duration: number; // em frames
  media: Media | null;
};

export type SceneKind = "image" | "title" | "date" | "quote" | "stat" | "list" | "clip";

export type Scene = {
  from: number; // frame inicial no vídeo
  duration: number; // em frames (sem a sobreposição da transição)
  kind: SceneKind;
  shots: Shot[];
  text?: string; // título do capítulo, data, citação, número...
  subtext?: string; // nº do capítulo, autor da citação, legenda do número...
  items?: string[]; // tópicos da cena "list"
  label?: string; // tarja inferior (lugar, pessoa, ano) nas cenas "image"
};

export type Caption = {
  start: number; // frame
  end: number;
  words: { start: number; text: string }[];
};

export type Timeline = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  title: string;
  scenes: Scene[];
  captions: Caption[];
  showCaptions: boolean;
};

// props da composição "Miniatura" (thumbnail do YouTube), gerada por app/render.py
export type ThumbnailProps = {
  title: string;
  image: Media | null;
};
