import { JSDOM } from "jsdom"; import { build } from "esbuild"; import fs from "fs";
const out = await build({ entryPoints:[new URL("./nav.entry.jsx", import.meta.url).pathname], bundle:true, write:false, format:"iife", jsx:"automatic", define:{"process.env.NODE_ENV":'"development"'} });
const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { runScripts:"outside-only", pretendToBeVisual:true });
const w = dom.window; w.MessageChannel=MessageChannel; w.__scrolls=0; w.scrollTo=()=>{w.__scrolls++};
let h=[]; w.__mql={matches:false,addEventListener:(t,f)=>h.push(f),removeEventListener:()=>{}}; w.__mqlFire=e=>h.forEach(f=>f(e)); w.matchMedia=()=>w.__mql;
w.eval(out.outputFiles[0].text);
const r = await w.__run();
// CSS
const css = fs.readFileSync(new URL("../app/globals.css", import.meta.url),"utf8").replace(/\s+/g," ");
const c=(n,re)=>r.push([n,re.test(css)]);
c("CSS : point de rupture 900 px",/@media \(max-width: 900px\)/); c("CSS : verrou de défilement",/body\.nav-open \{ overflow: hidden; \}/);
c("CSS : translateX(-101%)",/translateX\(-101%\)/); c("CSS : champs 16px",/textarea[^}]*font-size: 16px/); c("CSS : entrées 44px",/\.side a \{ min-height: 44px/);
c("CSS : grilles en colonne unique",/\.adm-grid \{ grid-template-columns: 1fr; \}/); c("CSS : tableaux en fiches",/table\.rt td\[data-label\]::before/);
r.forEach(([n,v])=>console.log(v?"OK  ":"ÉCHEC",n)); const p=r.filter(x=>x[1]).length; console.log(`\n${p}/${r.length} réussis`); process.exit(p===r.length?0:1);
