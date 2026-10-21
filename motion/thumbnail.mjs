// Renderiza a miniatura (thumbnail) do YouTube: um único quadro da composição "Miniatura".
// Uso: node thumbnail.mjs <config.json>
//   config = { publicDir, out, props: { title, image } }
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const inputProps = config.props;

const serveUrl = await bundle({ entryPoint: path.join(here, "src", "index.ts"), publicDir: config.publicDir });
const composition = await selectComposition({ serveUrl, id: "Miniatura", inputProps });
await renderStill({ composition, serveUrl, inputProps, output: config.out, imageFormat: "jpeg", jpegQuality: 90 });
