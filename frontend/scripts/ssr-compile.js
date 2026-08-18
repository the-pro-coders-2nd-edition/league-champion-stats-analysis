import { compile, preprocess } from 'svelte/compiler';
import sveltePreprocess from 'svelte-preprocess';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const tsPreprocessor = sveltePreprocess.typescript();

const SVELTE_IMPORT = /(from\s+['"])(\.[^'"]*?)\.svelte(['"])/g;

/**
 * Compile one component (and, recursively, any .svelte components it imports)
 * to sibling temp ESM modules, collecting each one's scoped CSS.
 *
 * Temp modules are written next to their source so relative imports keep
 * resolving; the .svelte specifier is rewritten to the compiled twin.
 *
 * @param {string} absPath
 * @param {string} stamp - shared suffix so one render's temp files clean up together
 * @param {Map<string, string>} written - absPath -> temp module path
 * @param {string[]} cssBlocks - accumulated scoped CSS, dependencies first
 * @returns {Promise<string>} temp module path for absPath
 */
async function compileToTemp(absPath, stamp, written, cssBlocks) {
  const existing = written.get(absPath);
  if (existing) return existing;

  const rawSource = fs.readFileSync(absPath, 'utf-8');
  const { code: source } = await preprocess(rawSource, tsPreprocessor, { filename: absPath });
  const { js, css } = compile(source, { generate: 'ssr', filename: absPath });

  const tmpFile = `${absPath}.ssr.${stamp}.mjs`;
  written.set(absPath, tmpFile);

  const dir = path.dirname(absPath);
  const deps = [...js.code.matchAll(SVELTE_IMPORT)].map((match) => match[2]);
  for (const specifier of deps) {
    await compileToTemp(path.resolve(dir, `${specifier}.svelte`), stamp, written, cssBlocks);
  }

  fs.writeFileSync(tmpFile, js.code.replace(SVELTE_IMPORT, `$1$2.svelte.ssr.${stamp}.mjs$3`));
  if (css && css.code) cssBlocks.push(css.code);
  return tmpFile;
}

/**
 * Compile a .svelte file to an SSR module and render it once with the given props.
 *
 * Used at dev-time / in CI only -- never at report-generation runtime. Props are
 * expected to be either real preview values (component development) or literal
 * Jinja tokens like "{{ rec.title }}" (template generation) -- see generate.js.
 *
 * @param {string} svelteFile - absolute or relative path to a .svelte component
 * @param {Record<string, unknown>} props
 * @returns {Promise<{ html: string, css: { code: string }, head: string }>}
 */
export async function renderComponent(svelteFile, props) {
  const absPath = path.resolve(svelteFile);
  const stamp = `${Date.now()}`;
  const written = new Map();
  const cssBlocks = [];

  const tmpFile = await compileToTemp(absPath, stamp, written, cssBlocks);
  try {
    const mod = await import(pathToFileURL(tmpFile).href);
    const rendered = mod.default.render(props);
    return { ...rendered, css: { code: cssBlocks.join('\n\n') } };
  } finally {
    for (const file of written.values()) {
      fs.unlinkSync(file);
    }
  }
}
