#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const { Resvg } = require("@resvg/resvg-js");

const [, , input, output, widthArg, heightArg, ...extraArgs] = process.argv;
const transparent = extraArgs.includes("--transparent");
const fontFiles = extraArgs.filter((item) => item !== "--transparent");

if (!input || !output || !widthArg || !heightArg) {
  console.error(
    "Usage: node scripts/render_svg.cjs input.svg output.png width height [font ...]"
  );
  process.exit(2);
}

const width = Number(widthArg);
const height = Number(heightArg);
if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
  throw new Error("width and height must be positive integers");
}

const svg = fs.readFileSync(input, "utf8");
const options = {
  fitTo: { mode: "width", value: width },
  background: transparent ? "rgba(0,0,0,0)" : "rgba(255,255,255,1)",
  font: {
    loadSystemFonts: true,
    fontFiles: fontFiles.map((item) => path.resolve(item)),
  },
};

const renderer = new Resvg(svg, options);
const rendered = renderer.render();

if (rendered.width !== width || rendered.height !== height) {
  throw new Error(
    `Unexpected raster size ${rendered.width}x${rendered.height}; expected ${width}x${height}`
  );
}

fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, rendered.asPng());
