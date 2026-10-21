#!/usr/bin/env node
// Copies the loading screen into dist/ so Wails can embed it.
//
// The admin panel is not part of this bundle: the desktop application serves
// it from the gateway over HTTP, so there is nothing to compile here. The
// build step only exists to satisfy the Wails asset pipeline.

const fs = require("fs");
const path = require("path");

const root = __dirname;
const src = path.join(root, "src");
const dist = path.join(root, "dist");

fs.mkdirSync(dist, { recursive: true });

for (const entry of fs.readdirSync(src)) {
  const from = path.join(src, entry);
  if (fs.statSync(from).isDirectory()) continue;
  fs.copyFileSync(from, path.join(dist, entry));
}

console.log("conduit frontend: copied", fs.readdirSync(dist).join(", "));
