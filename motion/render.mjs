// Renderiza o vídeo (sem áudio) em partes, uma por capítulo: se o render cair, as partes prontas são mantidas.
// Uso: node render.mjs <config.json>
//   config = { timeline, publicDir, concurrency, parts: [{ start, end, out }] }
// Escreve uma linha JSON por evento no stdout para o Python acompanhar o progresso.
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const inputProps = JSON.parse(fs.readFileSync(config.timeline, "utf-8"));
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");

const serveUrl = await bundle({ entryPoint: path.join(here, "src", "index.ts"), publicDir: config.publicDir });
const composition = await selectComposition({ serveUrl, id: "Documentario", inputProps });

const total = config.parts.reduce((n, p) => n + p.end - p.start + 1, 0);
let done = 0;
for (const [i, part] of config.parts.entries()) {
  const frames = part.end - part.start + 1;
  if (fs.existsSync(part.out)) {
    done += frames;
    emit({ part: i, skipped: true, progress: done / total });
    continue;
  }
  const tmp = part.out.replace(/\.mp4$/, ".tmp.mp4");
  await renderMedia({
    composition,
    serveUrl,
    inputProps,
    codec: "h264",
    crf: 20,
    muted: true,
    imageFormat: "jpeg",
    jpegQuality: 92,
    frameRange: [part.start, part.end],
    outputLocation: tmp,
    concurrency: config.concurrency ?? null,
    onProgress: ({ progress }) => emit({ part: i, progress: (done + progress * frames) / total }),
  });
  fs.renameSync(tmp, part.out);
  done += frames;
  emit({ part: i, finished: true, progress: done / total });
}
emit({ done: true });
