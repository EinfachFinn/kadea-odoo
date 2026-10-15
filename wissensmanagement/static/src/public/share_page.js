/** @odoo-module ignore */
// Rendert freigegebene Wissensseiten im Browser (marked + DOMPurify). Daten kommen aus #wm-data.
(function () {
  const D = JSON.parse(document.getElementById("wm-data").textContent);
  if (!window.marked) return;
  const lower = {};
  for (const p of D.pages.concat(D.files)) lower[p.toLowerCase()] = p;
  function resolve(t) {
    t = t.trim(); const c = /\.[a-z0-9]{1,6}$/i.test(t) ? [t] : [t + ".md", t];
    for (const x of c) {
      const l = x.toLowerCase(); if (lower[l]) return lower[l];
      const hit = Object.keys(lower).find((k) => k.endsWith("/" + l)); if (hit) return lower[hit];
    }
    return null;
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (m) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[m]);
  const wikilink = {
    name: "wikilink", level: "inline",
    start(s) { const i = s.indexOf("[["); return i < 0 ? undefined : (i > 0 && s[i - 1] === "!" ? i - 1 : i); },
    tokenizer(s) {
      const m = /^(!?)\[\[([^\[\]\n]+?)\]\]/.exec(s); if (!m) return;
      const [target, alias] = m[2].split("|"); return { type: "wikilink", raw: m[0], embed: !!m[1], target: target.split("#")[0], alias };
    },
    renderer(t) {
      const r = resolve(t.target); const label = esc(t.alias || t.target.split("/").pop());
      if (t.embed && r && /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(r))
        return `<img src="${D.fileBase}${encodeURIComponent(r)}" alt="${label}"${/^\d+$/.test(t.alias || "") ? ` width="${t.alias}"` : ""}/>`;
      if (r && D.links[r]) return `<a class="wl" href="${D.pageBase}${D.links[r]}">${label}</a>`;
      if (r && !r.endsWith(".md")) return `<a href="${D.fileBase}${encodeURIComponent(r)}">📎 ${label}</a>`;
      return `<span class="wl">${label}</span>`;
    },
  };
  const hl = { name: "hl", level: "inline", start(s) { const i = s.indexOf("=="); return i < 0 ? undefined : i; },
    tokenizer(s) { const m = /^==(?=\S)([^\n]*?\S)==/.exec(s); if (m) return { type: "hl", raw: m[0], text: m[1] }; },
    renderer(t) { return `<mark>${esc(t.text)}</mark>`; } };
  const md = new marked.Marked({ gfm: true, breaks: true });
  md.use({ extensions: [wikilink, hl], renderer: {
    link({ href, tokens }) {
      const text = this.parser.parseInline(tokens);
      if (/^odoo:\/\//.test(href)) return `<span class="mention">${text}</span>`;
      return `<a href="${esc(href)}" rel="noopener noreferrer">${text}</a>`;
    },
    image({ href, text }) {
      const r = /^[a-z]+:/i.test(href) ? null : resolve(decodeURIComponent(href));
      return `<img src="${r ? D.fileBase + encodeURIComponent(r) : esc(href)}" alt="${esc(text)}"/>`;
    },
  } });
  let src = D.content;
  const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(src);
  if (fm) src = src.slice(fm[0].length);
  let html = md.parse(src);
  html = html.replace(/<blockquote>\s*<p>\[!((?!columns\b|col\b|center\b)[\w-]+)(?:\|[^\]]*)?\][+-]?\s*/g, (m, type) => `<blockquote class="callout"><p><b>${type.toUpperCase()}</b> `);
  const box = document.getElementById("c");
  box.innerHTML = DOMPurify.sanitize(html);
  // Layout-Boxen: Spalten nebeneinander und zentrierte Bereiche
  for (const bq of [...box.querySelectorAll("blockquote")]) {
    const first = bq.firstElementChild;
    const lm = first && first.tagName === "P" && /^\[!(columns|col|center)(?:\|([^\]]*))?\][+-]?\s*(?:<br>\s*)?/i.exec(first.innerHTML);
    if (!lm) continue;
    const kind = lm[1].toLowerCase();
    const div = document.createElement("div");
    div.className = kind === "columns" ? "cols cols-" + ((lm[2] || "eq").replace(/[^0-9-]/g, "") || "eq") : kind === "col" ? "col" : "center";
    first.innerHTML = first.innerHTML.slice(lm[0].length);
    if (!first.innerHTML.trim()) first.remove();
    div.append(...bq.childNodes);
    bq.replaceWith(div);
  }
  const first = box.querySelector("h1");
  if (first && first.textContent.trim() === document.getElementById("t").textContent.trim()) first.remove();
  const load = (src) => new Promise((res) => { const sc = document.createElement("script"); sc.src = src; sc.onload = res; sc.onerror = res; document.head.appendChild(sc); });
  const jobs = [];
  const mm = box.querySelectorAll("pre > code.language-mermaid");
  if (mm.length) jobs.push(load("/wissensmanagement/static/lib/mermaid/mermaid.min.js").then(async () => {
    if (!window.mermaid) return;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
    let i = 0;
    for (const code of mm) {
      try { const r = await mermaid.render("mm" + (i++), code.textContent); const d = document.createElement("div"); d.style.textAlign = "center"; d.innerHTML = r.svg; code.parentElement.replaceWith(d); } catch (e) {}
    }
  }));
  const code = box.querySelectorAll("pre > code[class*='language-']:not(.language-mermaid)");
  if (code.length) {
    const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "/web/static/lib/prismjs/themes/okaida.css"; document.head.appendChild(css);
    window.Prism = window.Prism || { manual: true };
    jobs.push(load("/web/static/lib/prismjs/prism.js").then(() => window.Prism && code.forEach((c) => Prism.highlightElement(c))));
  }
  if (D.print) Promise.all(jobs).then(() => setTimeout(() => window.print(), 400));
})();
