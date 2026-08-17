import { compile } from 'svelte/compiler';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

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
  const source = fs.readFileSync(absPath, 'utf-8');
  const { js } = compile(source, { generate: 'ssr', filename: absPath });

  const tmpFile = `${absPath}.ssr.${Date.now()}.mjs`;
  fs.writeFileSync(tmpFile, js.code);
  try {
    const mod = await import(pathToFileURL(tmpFile).href);
    return mod.default.render(props);
  } finally {
    fs.unlinkSync(tmpFile);
  }
}
