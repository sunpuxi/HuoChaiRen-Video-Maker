#!/usr/bin/env node

// Batch rasteriser for per-frame animation. One process for the whole frame
// sequence: instantiating a native renderer per frame would re-scan the system
// fonts every time (~0.5 s/frame) instead of ~60 ms/frame.
//
// Usage: node scripts/render_svg_batch.cjs manifest.json
// Manifest: [{ "svg": "...", "png": "...", "width": 3840, "height": 2160,
//              "transparent": true, "fonts": ["/path/font.ttf"] }]

const fs = require("node:fs");
const path = require("node:path");
const { Resvg } = require("@resvg/resvg-js");

const [, , manifestPath] = process.argv;
if (!manifestPath) {
  console.error("Usage: node scripts/render_svg_batch.cjs manifest.json");
  process.exit(2);
}

const items = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
for (const item of items) {
  const svg = fs.readFileSync(item.svg, "utf8");
  const width = Number(item.width);
  const height = Number(item.height);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    throw new Error("width and height must be positive integers");
  }
  // Text-free layers skip the system font scan; anything with <text> keeps it.
  const needsFonts = item.systemFonts !== false || svg.includes("<text");
  const renderer = new Resvg(svg, {
    fitTo: { mode: "width", value: width },
    background: item.transparent ? "rgba(0,0,0,0)" : "rgba(255,255,255,1)",
    font: {
      loadSystemFonts: needsFonts,
      fontFiles: (item.fonts || []).map((value) => path.resolve(value)),
    },
  });
  const rendered = renderer.render();
  if (rendered.width !== width || rendered.height !== height) {
    throw new Error(`Unexpected raster size ${rendered.width}x${rendered.height}; expected ${width}x${height}`);
  }
  fs.mkdirSync(path.dirname(item.png), { recursive: true });
  fs.writeFileSync(item.png, rendered.asPng());
}
