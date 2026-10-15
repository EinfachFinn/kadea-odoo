/* global Prism, mermaid */
// Zusätzliche Aufbereitung des Lesemodus: eingebettete Seiten (![[Seite]]), Mermaid-Diagramme,
// Code-Highlighting, "→ Odoo-Aufgabe"-Knöpfe an Checkboxen und Markierung kommentierter Stellen.
import { loadCSS, loadJS } from "@web/core/assets";
import { escapeHtml, renderMarkdown, slugify } from "./markdown";
import { decorateMention } from "./mention_cards";

const MAX_EMBED_DEPTH = 2;
let mermaidReady = null;
let prismReady = null;

/** Abschnitt "## Überschrift" bis zur nächsten gleich-/höherrangigen Überschrift. */
function extractSection(md, anchor) {
    if (!anchor) {
        return md;
    }
    const lines = md.split("\n");
    let start = -1;
    let level = 0;
    for (let i = 0; i < lines.length; i++) {
        const m = /^(#{1,6})\s+(.*)$/.exec(lines[i]);
        if (!m) {
            continue;
        }
        if (start < 0 && slugify(m[2]) === anchor) {
            start = i;
            level = m[1].length;
        } else if (start >= 0 && m[1].length <= level) {
            return lines.slice(start, i).join("\n");
        }
    }
    return start >= 0 ? lines.slice(start).join("\n") : md;
}

async function renderEmbeds(root, ctx, depth) {
    const links = [...root.querySelectorAll("a.o_wm_wikilink_embed[data-wm-path]")].filter((a) =>
        /\.md$/i.test(a.dataset.wmPath)
    );
    for (const a of links) {
        const path = a.dataset.wmPath;
        const box = document.createElement("div");
        box.className = "o_wm_embed";
        if (depth >= MAX_EMBED_DEPTH || ctx.seen.has(path)) {
            continue; // Endlosschleifen (Seite bettet sich selbst ein) vermeiden
        }
        let page;
        try {
            page = await ctx.orm.silent.call("wissen.vault", "read_page", [path]);
        } catch {
            a.classList.add("o_wm_wikilink_missing");
            continue;
        }
        const md = extractSection(page.content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, ""), a.dataset.wmAnchor);
        const { html } = renderMarkdown(md, { index: ctx.index, path });
        box.innerHTML =
            `<div class="o_wm_embed_head"><a href="#" class="o_wm_wikilink" data-wm-path="${escapeHtml(path)}">` +
            `<i class="fa fa-external-link me-1"></i>${escapeHtml(page.name)}</a></div>` +
            `<div class="o_wm_content o_wm_embed_body">${html}</div>`;
        for (const cb of box.querySelectorAll("input.o_wm_task")) {
            cb.disabled = true; // Checkboxen nur auf der Originalseite umschalten
        }
        const p = a.parentElement;
        if (p && p.tagName === "P" && p.textContent.trim() === a.textContent.trim()) {
            p.replaceWith(box);
        } else {
            a.replaceWith(box);
        }
        ctx.seen.add(path);
        for (const m of box.querySelectorAll(".o_wm_mention")) {
            decorateMention(ctx.orm, m);
        }
        await renderEmbeds(box, ctx, depth + 1);
    }
}

async function renderMermaid(root) {
    const blocks = [...root.querySelectorAll("pre > code.language-mermaid")];
    if (!blocks.length) {
        return;
    }
    if (!mermaidReady) {
        mermaidReady = loadJS("/wissensmanagement/static/lib/mermaid/mermaid.min.js").then(() => {
            mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
        });
    }
    await mermaidReady;
    for (const code of blocks) {
        const pre = code.parentElement;
        const id = `wm-mermaid-${Math.random().toString(36).slice(2)}`;
        try {
            const { svg } = await mermaid.render(id, code.textContent);
            const div = document.createElement("div");
            div.className = "o_wm_mermaid";
            div.innerHTML = svg;
            pre.replaceWith(div);
        } catch (e) {
            pre.classList.add("o_wm_mermaid_error");
            pre.title = String(e?.message || e);
        }
        document.getElementById("d" + id)?.remove();
    }
}

async function highlightCode(root) {
    const blocks = [...root.querySelectorAll("pre > code[class*='language-']")].filter(
        (c) => !c.classList.contains("language-mermaid")
    );
    if (!blocks.length) {
        return;
    }
    if (!prismReady) {
        window.Prism = window.Prism || { manual: true };
        prismReady = Promise.all([
            loadJS("/web/static/lib/prismjs/prism.js"),
            loadCSS("/web/static/lib/prismjs/themes/okaida.css"),
        ]);
    }
    await prismReady;
    for (const code of blocks) {
        Prism.highlightElement(code);
    }
}

function addTaskButtons(root) {
    const tasks = [...root.querySelectorAll("input.o_wm_task")].filter(
        (cb) => !cb.closest(".o_wm_embed")
    );
    tasks.forEach((cb, i) => {
        const li = cb.closest("li");
        if (!li || li.querySelector(":scope > .o_wm_task_to_odoo") || cb.checked) {
            return;
        }
        if (li.querySelector('.o_wm_mention[data-wm-model="project.task"]')) {
            return; // schon mit einer Aufgabe verknüpft
        }
        const clone = li.cloneNode(true);
        clone.querySelectorAll("ul, ol, input").forEach((n) => n.remove());
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "o_wm_task_to_odoo";
        btn.title = "Als Odoo-Aufgabe anlegen";
        btn.dataset.taskIndex = String(i);
        btn.dataset.taskText = clone.textContent.trim();
        btn.innerHTML = '<i class="fa fa-check-square-o me-1"></i>Aufgabe';
        const sub = li.querySelector(":scope > ul, :scope > ol");
        li.insertBefore(btn, sub || null);
    });
}

/** Alles nach dem Rendern einer Seite im Lesemodus. */
export async function decorateReadView(el, { orm, index, path, canCreateTasks }) {
    const content = el.querySelector(".o_wm_page > .o_wm_content");
    if (!content) {
        return;
    }
    if (canCreateTasks) {
        addTaskButtons(content);
    }
    await renderEmbeds(content, { orm, index, seen: new Set([path]) }, 0);
    await Promise.all([renderMermaid(content), highlightCode(content)]);
}

/** Markiert die Textstellen, zu denen es offene Kommentare gibt. */
export function highlightQuotes(el, quotes) {
    const root = el?.querySelector?.(".o_wm_page > .o_wm_content") || el;
    if (!root) {
        return;
    }
    for (const mark of root.querySelectorAll("mark.o_wm_comment_mark")) {
        mark.replaceWith(...mark.childNodes);
    }
    root.normalize();
    for (const quote of quotes || []) {
        const q = quote.trim();
        if (!q) {
            continue;
        }
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const idx = node.textContent.indexOf(q.split("\n")[0]);
            if (idx >= 0) {
                const range = document.createRange();
                range.setStart(node, idx);
                range.setEnd(node, Math.min(node.textContent.length, idx + q.split("\n")[0].length));
                const mark = document.createElement("mark");
                mark.className = "o_wm_comment_mark";
                mark.title = "Kommentiert";
                range.surroundContents(mark);
                break;
            }
        }
    }
}
