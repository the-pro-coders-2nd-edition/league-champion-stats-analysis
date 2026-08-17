import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderComponent } from './ssr-compile.js';

// generate.js -- reads manifest.json, SSR-renders each component with the props
// given (typically literal Jinja tokens like "{{ rec.title }}"), and writes the
// resulting HTML under src/league_stats/presentation/templates/generated/. Run
// after editing any .svelte file; CI re-runs this and diffs the output against
// what's committed to catch anyone who forgot.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const TEMPLATES_DIR = path.resolve(ROOT, '../src/league_stats/presentation/templates');
const GENERATED_DIR = path.join(TEMPLATES_DIR, 'generated');
const MANIFEST_PATH = path.join(ROOT, 'manifest.json');

async function main() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'));
  fs.mkdirSync(GENERATED_DIR, { recursive: true });

  // Svelte's scoped-CSS output is deterministic per compiled component, so
  // deduping by exact text content is sufficient even when a shared
  // sub-component's CSS shows up inside multiple top-level renders.
  const cssBlocks = new Set();
  let fileCount = 0;

  for (const [name, entry] of Object.entries(manifest)) {
    const componentPath = path.resolve(ROOT, entry.component);
    for (const output of entry.outputs) {
      const { html, css } = await renderComponent(componentPath, output.props);
      const outPath = path.join(GENERATED_DIR, output.file);
      fs.writeFileSync(outPath, html);
      fileCount += 1;
      if (css && css.code) cssBlocks.add(css.code);
      console.log(`generated ${path.relative(ROOT, outPath)} from ${name}`);
    }
  }

  if (cssBlocks.size > 0) {
    const combinedCss = Array.from(cssBlocks).join('\n\n');
    fs.writeFileSync(path.join(GENERATED_DIR, 'components.css'), combinedCss);
  }

  console.log(`done: ${fileCount} template(s), ${cssBlocks.size} unique CSS block(s)`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
