import { readFile, writeFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
const root = resolve(process.argv[2]);
const views = ['login','register','overview','analysis','visualizations','settings'];
const stages = ['reference','before','after'];
const sections = []; const hashes = {};
for (const width of [1440,390]) for (const locale of ['en','zh-CN']) for (const view of views) {
  const filename = `${view}-${locale}-${width}.png`;
  const figures = [];
  for (const stage of stages) {
    const path = `${stage}/${filename}`;
    hashes[path] = createHash('sha256').update(await readFile(resolve(root,path))).digest('hex');
    figures.push(`<figure><figcaption>${stage}</figcaption><a href="${path}"><img loading="lazy" src="${path}" alt="${stage} ${view} ${locale} ${width}px"></a></figure>`);
  }
  sections.push(`<section data-width="${width}" data-locale="${locale}"><h2>${view} · ${locale} · ${width}px</h2><div class="compare">${figures.join('')}</div></section>`);
}
const interactions = (await readdir(resolve(root,'interaction'))).filter(name => name.endsWith('.png'));
for (const name of interactions) hashes[`interaction/${name}`] = createHash('sha256').update(await readFile(resolve(root,'interaction',name))).digest('hex');
await writeFile(resolve(root,'screenshot-sha256.json'),JSON.stringify(hashes,null,2));
await writeFile(resolve(root,'index.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase 5.8a · 视觉审阅</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f7fb;color:#1a2944;margin:24px}header{position:sticky;top:0;background:#fff;padding:16px;border-radius:12px;z-index:1;box-shadow:0 3px 20px #1231}h1{font-size:22px}p{line-height:1.6}select{padding:10px;margin:0 12px 0 4px}section{margin:32px 0}.compare{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:start}figure{margin:0;background:#fff;border:1px solid #ccd7e8}figcaption{padding:12px;font-weight:bold}img{width:100%;height:auto;display:block}a{color:#3155ad}h2{font-size:18px}@media(max-width:700px){.compare{grid-template-columns:1fr}}[hidden]{display:none!important}</style>
<header><h1>Workspace v4.1 · Phase 5.8a 视觉审阅</h1><p>左：附件原型；中：优化前真实前端；右：优化后真实前端。点击图片查看原尺寸。所有内容均为 synthetic demo，无后端或付费调用。</p><label>视口 <select id="width"><option value="all">全部</option><option>1440</option><option>390</option></select></label><label>语言 <select id="locale"><option value="all">全部</option><option>en</option><option>zh-CN</option></select></label><a href="review.md">差异报告</a> · <a href="interaction/ux-results.json">119 项 UX 结果</a></header>
${sections.join('')}<h2>交互与 768px 检查</h2><ul>${interactions.map(name=>`<li><a href="interaction/${name}">${name}</a></li>`).join('')}</ul><script>for(const id of ['width','locale'])document.getElementById(id).addEventListener('change',()=>{for(const section of document.querySelectorAll('section'))section.hidden=['width','locale'].some(key=>document.getElementById(key).value!=='all'&&document.getElementById(key).value!==section.dataset[key]);});</script></html>`);
console.log(`Gallery ready: ${Object.keys(hashes).length} screenshots with SHA-256.`);
