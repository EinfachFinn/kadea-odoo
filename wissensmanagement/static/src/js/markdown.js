/* global marked, DOMPurify */
// Markdown -> HTML mit Erweiterungen für Wissensseiten:
//   [[Seite]], [[Seite|Alias]], [[Seite#Abschnitt]], ![[bild.png|300]]   -> Vault-Links / Einbettungen
//   [@Name](odoo://res.partner/42)                                        -> @-Erwähnung eines Odoo-Datensatzes
//   ==markiert==, #tag, > [!note] Callouts, YAML-Frontmatter, GFM-Tabellen & Checklisten

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;
const CALLOUT_ICONS = {
    note: "fa-pencil", info: "fa-info-circle", tip: "fa-lightbulb-o", hint: "fa-lightbulb-o",
    important: "fa-exclamation-circle", success: "fa-check-circle", check: "fa-check-circle",
    done: "fa-check-circle", question: "fa-question-circle", faq: "fa-question-circle",
    warning: "fa-exclamation-triangle", caution: "fa-exclamation-triangle", attention: "fa-exclamation-triangle",
    danger: "fa-bolt", error: "fa-bolt", bug: "fa-bug", example: "fa-list", quote: "fa-quote-left",
    abstract: "fa-file-text-o", summary: "fa-file-text-o", todo: "fa-check-square-o",
};

export function escapeHtml(str) {
    return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

export function slugify(text) {
    return String(text)
        .toLowerCase()
        .trim()
        .replace(/<[^>]+>/g, "")
        .replace(/[^\p{L}\p{N}\s-]/gu, "")
        .replace(/\s+/g, "-");
}

export function dirname(path) {
    const i = (path || "").lastIndexOf("/");
    return i < 0 ? "" : path.slice(0, i);
}

export function basename(path, stripMd = true) {
    const name = (path || "").split("/").pop();
    return stripMd && name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

export function fileUrl(path) {
    return `/wissensmanagement/file?path=${encodeURIComponent(path)}`;
}

/**
 * Index aller Wissensseiten für die Link-Auflösung (Name, Pfad oder Pfad-Ende).
 */
export class LinkIndex {
    constructor(files) {
        this.rebuild(files);
    }

    rebuild(files) {
        this.files = files || [];
        this.byName = new Map();
        this.lower = new Map();
        for (const f of this.files) {
            const lower = f.toLowerCase();
            this.lower.set(lower, f);
            const name = lower.split("/").pop();
            if (!this.byName.has(name)) {
                this.byName.set(name, []);
            }
            this.byName.get(name).push(f);
        }
    }

    /** Liefert den Vault-Pfad zu einem Link-Ziel oder null. */
    resolve(target, fromPath = "") {
        let t = (target || "").trim().replace(/^\/+/, "");
        try {
            t = decodeURIComponent(t);
        } catch {
            // ungültiges %-Encoding: Ziel unverändert verwenden
        }
        if (!t) {
            return null;
        }
        const candidates = /\.md$/i.test(t) ? [t] : [t + ".md", t];
        const fromDir = dirname(fromPath);
        for (const c of candidates) {
            const lc = c.toLowerCase();
            // 1. relativ zur aktuellen Seite (Markdown-Links), 2. absolut im Vault
            const rel = normalize((fromDir ? fromDir + "/" : "") + c).toLowerCase();
            if (this.lower.has(rel)) {
                return this.lower.get(rel);
            }
            if (this.lower.has(lc)) {
                return this.lower.get(lc);
            }
            // 3. nach Dateiname bzw. Pfad-Ende (kürzeste eindeutige Form)
            const name = lc.split("/").pop();
            const matches = (this.byName.get(name) || []).filter(
                (f) => !lc.includes("/") || f.toLowerCase().endsWith("/" + lc)
            );
            if (matches.length) {
                const sameDir = matches.find((m) => dirname(m) === fromDir);
                return sameDir || [...matches].sort((a, b) => a.length - b.length)[0];
            }
        }
        return null;
    }

    add(path) {
        this.rebuild([...this.files, path]);
    }

    pages() {
        return this.files.filter((f) => f.toLowerCase().endsWith(".md"));
    }
}

function normalize(path) {
    const out = [];
    for (const part of path.split("/")) {
        if (part === "..") {
            out.pop();
        } else if (part && part !== ".") {
            out.push(part);
        }
    }
    return out.join("/");
}

function parseFrontmatter(src) {
    const m = src.match(FRONTMATTER);
    if (!m) {
        return { body: src, props: null };
    }
    const props = [];
    let last = null;
    for (const line of m[1].split(/\r?\n/)) {
        const kv = line.match(/^([^:\s][^:]*):\s*(.*)$/);
        if (kv) {
            last = { key: kv[1].trim(), value: kv[2].trim() };
            props.push(last);
        } else if (last && /^\s*-\s+/.test(line)) {
            last.value += (last.value ? ", " : "") + line.replace(/^\s*-\s+/, "");
        }
    }
    return { body: src.slice(m[0].length), props };
}

/**
 * Rendert Markdown zu sicherem HTML.
 * @returns {{ html: string, headings: {level:number, text:string, id:string}[] }}
 */
export function renderMarkdown(src, { index, path = "" } = {}) {
    index = index || new LinkIndex([]);
    const headings = [];
    const slugCount = {};
    const { body, props } = parseFrontmatter(src || "");

    const wikilink = {
        name: "wikilink",
        level: "inline",
        start(s) {
            const i = s.indexOf("[[");
            if (i < 0) {
                return undefined;
            }
            return i > 0 && s[i - 1] === "!" ? i - 1 : i;
        },
        tokenizer(s) {
            const m = /^(!?)\[\[([^\[\]\n]+?)\]\]/.exec(s);
            if (!m) {
                return undefined;
            }
            const [full, bang, inner] = m;
            const pipe = inner.indexOf("|");
            const targetFull = (pipe >= 0 ? inner.slice(0, pipe) : inner).trim();
            const alias = pipe >= 0 ? inner.slice(pipe + 1).trim() : "";
            const hashIdx = targetFull.search(/[#^]/);
            const target = hashIdx >= 0 ? targetFull.slice(0, hashIdx) : targetFull;
            const anchor = hashIdx >= 0 ? targetFull.slice(hashIdx + 1) : "";
            return { type: "wikilink", raw: full, embed: !!bang, target, anchor, alias };
        },
        renderer(tok) {
            const resolved = tok.target ? index.resolve(tok.target, path) : path;
            const label = tok.alias || (tok.target ? basename(tok.target) : "") + (tok.anchor ? ` › ${tok.anchor}` : "") || tok.anchor;
            if (tok.embed && resolved && IMAGE_EXT.test(resolved)) {
                const size = /^\d+(x\d+)?$/.test(tok.alias) ? tok.alias.split("x") : null;
                const sizeAttr = size ? ` width="${size[0]}"${size[1] ? ` height="${size[1]}"` : ""}` : "";
                const alt = size ? basename(resolved, false) : tok.alias || basename(resolved, false);
                return `<img class="o_wm_embed_img" src="${escapeHtml(fileUrl(resolved))}" alt="${escapeHtml(alt)}"${sizeAttr}/>`;
            }
            if (resolved && !resolved.toLowerCase().endsWith(".md")) {
                return `<a class="o_wm_filelink" href="${escapeHtml(fileUrl(resolved))}" target="_blank">` +
                    `<i class="fa fa-paperclip me-1"></i>${escapeHtml(label)}</a>`;
            }
            const cls = ["o_wm_wikilink", resolved ? "" : "o_wm_wikilink_missing", tok.embed ? "o_wm_wikilink_embed" : ""]
                .filter(Boolean)
                .join(" ");
            const title = resolved ? resolved.replace(/\.md$/i, "") : `Seite „${tok.target}“ anlegen`;
            return `<a href="#" class="${cls}" title="${escapeHtml(title)}" data-wm-path="${escapeHtml(resolved || "")}"` +
                ` data-wm-target="${escapeHtml(tok.target)}" data-wm-anchor="${escapeHtml(slugify(tok.anchor))}">` +
                `${tok.embed ? '<i class="fa fa-file-text-o me-1"></i>' : ""}${escapeHtml(label)}</a>`;
        },
    };

    const highlight = {
        name: "highlight",
        level: "inline",
        start(s) {
            const i = s.indexOf("==");
            return i < 0 ? undefined : i;
        },
        tokenizer(s) {
            const m = /^==(?=\S)([^\n]*?\S)==/.exec(s);
            if (m) {
                return { type: "highlight", raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
            }
            return undefined;
        },
        renderer(tok) {
            return `<mark>${this.parser.parseInline(tok.tokens)}</mark>`;
        },
    };

    const tag = {
        name: "tag",
        level: "inline",
        start(s) {
            const m = /(^|\s)#[\p{L}_][\p{L}\p{N}_\-/]*/u.exec(s);
            return m ? m.index + m[1].length : undefined;
        },
        tokenizer(s) {
            const m = /^#([\p{L}_][\p{L}\p{N}_\-/]*)/u.exec(s);
            return m ? { type: "tag", raw: m[0], tag: m[1] } : undefined;
        },
        renderer(tok) {
            return `<a href="#" class="o_wm_tag" data-wm-tag="${escapeHtml(tok.tag)}">#${escapeHtml(tok.tag)}</a>`;
        },
    };

    const renderer = {
        heading({ tokens, depth, text }) {
            const inner = this.parser.parseInline(tokens);
            let id = slugify(text) || "abschnitt";
            if (slugCount[id] !== undefined) {
                id = `${id}-${++slugCount[id]}`;
            } else {
                slugCount[id] = 0;
            }
            headings.push({ level: depth, text: text.replace(/\[\[(?:[^\]|]*\|)?([^\]]*)\]\]/g, "$1"), id });
            return `<h${depth} id="${escapeHtml(id)}">${inner}</h${depth}>\n`;
        },
        link({ href, title, tokens }) {
            const text = this.parser.parseInline(tokens);
            const mention = /^odoo:\/\/([a-z0-9_.]+)\/(\d+)/i.exec(href || "");
            if (mention) {
                return `<a href="#" class="o_wm_mention" data-wm-model="${escapeHtml(mention[1])}" data-wm-id="${mention[2]}">${text}</a>`;
            }
            if (href && !/^([a-z][a-z0-9+.-]*:|#|\/\/)/i.test(href)) {
                const resolved = index.resolve(href.split("#")[0], path);
                if (resolved && resolved.toLowerCase().endsWith(".md")) {
                    return `<a href="#" class="o_wm_wikilink" data-wm-path="${escapeHtml(resolved)}">${text}</a>`;
                }
                if (resolved) {
                    return `<a class="o_wm_filelink" href="${escapeHtml(fileUrl(resolved))}" target="_blank">${text}</a>`;
                }
            }
            const t = title ? ` title="${escapeHtml(title)}"` : "";
            return `<a href="${escapeHtml(href)}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`;
        },
        image({ href, title, text }) {
            let src = href || "";
            if (src && !/^([a-z][a-z0-9+.-]*:|\/\/)/i.test(src)) {
                const resolved = index.resolve(src, path);
                src = resolved ? fileUrl(resolved) : src;
            }
            const t = title ? ` title="${escapeHtml(title)}"` : "";
            return `<img src="${escapeHtml(src)}" alt="${escapeHtml(text)}"${t}/>`;
        },
        checkbox({ checked }) {
            return `<input type="checkbox" class="o_wm_task form-check-input me-2"${checked ? " checked" : ""}/>`;
        },
    };

    const md = new marked.Marked({ gfm: true, breaks: true });
    md.use({ extensions: [wikilink, highlight, tag], renderer });
    let html = md.parse(body);

    if (props && props.length) {
        const rows = props
            .map((p) => `<tr><th>${escapeHtml(p.key)}</th><td>${escapeHtml(p.value)}</td></tr>`)
            .join("");
        html = `<table class="o_wm_properties"><tbody>${rows}</tbody></table>` + html;
    }

    html = DOMPurify.sanitize(html, { ADD_ATTR: ["target"], FORBID_TAGS: ["style", "form"] });
    return { html: decorateCallouts(html), headings };
}

/** Wandelt Hinweisboxen (> [!warning] Titel) in gestylte Boxen um. */
function decorateCallouts(html) {
    if (!html.includes("[!")) {
        return html;
    }
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    for (const bq of doc.querySelectorAll("blockquote")) {
        const first = bq.firstElementChild;
        if (!first || first.tagName !== "P") {
            continue;
        }
        const lm = /^\[!(columns|col|center)(?:\|([^\]]*))?\][+-]?\s*(?:<br>\s*)?/i.exec(first.innerHTML);
        if (lm) {
            // Layout-Boxen: Spalten nebeneinander bzw. zentrierter Bereich (ohne Kasten/Titel)
            const kind = lm[1].toLowerCase();
            const meta = (lm[2] || "").replace(/[^0-9-]/g, "");
            const box = doc.createElement("div");
            box.className = { columns: `o_wm_columns o_wm_cols_${meta || "eq"}`, col: "o_wm_col", center: "o_wm_center_block" }[kind];
            first.innerHTML = first.innerHTML.slice(lm[0].length);
            if (!first.innerHTML.trim()) {
                first.remove();
            }
            box.append(...bq.childNodes);
            bq.replaceWith(box);
            continue;
        }
        const m = /^\[!([\w-]+)(?:\|[^\]]*)?\]([+-]?)[ \t]*([^\n<]*)/.exec(first.innerHTML);
        if (!m) {
            continue;
        }
        const type = m[1].toLowerCase();
        const box = doc.createElement(m[2] ? "details" : "div");
        box.className = `o_wm_callout o_wm_callout_${type}`;
        if (m[2] === "+") {
            box.setAttribute("open", "");
        }
        const title = doc.createElement(m[2] ? "summary" : "div");
        title.className = "o_wm_callout_title";
        title.innerHTML = `<i class="fa ${CALLOUT_ICONS[type] || "fa-pencil"} me-2"></i>`;
        // m[3] stammt aus bereits bereinigtem HTML und enthält kein "<" (nur Text/Entities)
        title.insertAdjacentHTML("beforeend", m[3].trim() || type.charAt(0).toUpperCase() + type.slice(1));
        first.innerHTML = first.innerHTML.slice(m[0].length).replace(/^\s*<br>\s*/, "");
        if (!first.innerHTML.trim()) {
            first.remove();
        }
        const content = doc.createElement("div");
        content.className = "o_wm_callout_content";
        content.append(...bq.childNodes);
        box.append(title, content);
        bq.replaceWith(box);
    }
    return doc.body.firstElementChild.innerHTML;
}

/** Schaltet die n-te Checkbox (- [ ] / - [x]) im Markdown-Quelltext um. */
export function toggleTask(src, n) {
    let i = -1;
    let inFence = false;
    const lines = src.split("\n");
    for (let l = 0; l < lines.length; l++) {
        if (/^\s*(```|~~~)/.test(lines[l])) {
            inFence = !inFence;
            continue;
        }
        if (inFence) {
            continue;
        }
        const m = /^(\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])/.exec(lines[l]);
        if (m && ++i === n) {
            lines[l] = m[1] + (m[2] === " " ? "x" : " ") + m[3] + lines[l].slice(m[0].length);
            break;
        }
    }
    return lines.join("\n");
}
