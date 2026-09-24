import * as esbuild from "esbuild";
import { mkdirSync } from "fs";

mkdirSync("../api/static/r3f", { recursive: true });

await esbuild.build({
  entryPoints: ["src/main.tsx"],
  bundle: true,
  format: "esm",
  outfile: "../api/static/r3f/campus.js",
  jsx: "automatic",
  loader: { ".tsx": "tsx", ".ts": "ts", ".css": "css" },
  define: { "import.meta.env.VITE_API_URL": '""', "process.env.NODE_ENV": '"production"' },
  minify: true,
  logLevel: "info",
});
console.log("bundled -> apps/api/static/r3f/campus.js");
