/* global toastui */
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { basename, fileUrl, slugify } from "./markdown";
import { decorateMention } from "./mention_cards";
import { PromptDialog } from "./dialogs";
import { CollabSync, userColor } from "./collab_sync";
import { user } from "@web/core/user";

// Slash-Menü: "/" am Zeilenanfang oder nach einem Leerzeichen
const SLASH_COMMANDS = [
    { id: "h1", label: "Überschrift 1", icon: "fa-header", keys: "h1 überschrift titel heading" },
    { id: "h2", label: "Überschrift 2", icon: "fa-header", keys: "h2 überschrift heading" },
    { id: "h3", label: "Überschrift 3", icon: "fa-header", keys: "h3 überschrift heading" },
    { id: "ul", label: "Aufzählung", icon: "fa-list-ul", keys: "liste aufzählung bullet ul" },
    { id: "ol", label: "Nummerierte Liste", icon: "fa-list-ol", keys: "nummer liste ol zahlen" },
    { id: "task", label: "Checkliste", icon: "fa-check-square-o", keys: "checkliste todo aufgaben checkbox" },
    { id: "table", label: "Tabelle", icon: "fa-table", keys: "tabelle table" },
    { id: "hr", label: "Trennlinie", icon: "fa-minus", keys: "trennlinie linie divider hr abschnitt" },
    { id: "quote", label: "Zitat", icon: "fa-quote-right", keys: "zitat quote" },
    { id: "callout", label: "Hinweisbox", icon: "fa-info-circle", keys: "hinweis callout info box tipp warnung" },
    { id: "cols2", label: "2 Spalten nebeneinander", icon: "fa-columns", keys: "spalten 2 split nebeneinander columns" },
    { id: "cols3", label: "3 Spalten nebeneinander", icon: "fa-columns", keys: "spalten 3 split nebeneinander columns" },
    { id: "center", label: "Zentriert", icon: "fa-align-center", keys: "zentriert zentrieren mitte center ausrichtung" },
    { id: "code", label: "Code-Block", icon: "fa-code", keys: "code block" },
    { id: "mermaid", label: "Diagramm (Mermaid)", icon: "fa-sitemap", keys: "diagramm mermaid flowchart" },
    { id: "page", label: "Link zu Seite", icon: "fa-book", keys: "seite link wikilink verlinken" },
    { id: "mention", label: "Odoo-Datensatz erwähnen", icon: "fa-at", keys: "erwähnen mention kontakt @" },
    { id: "task_mention", label: "Aufgabe erwähnen", icon: "fa-tasks", keys: "aufgabe erwähnen task" },
    { id: "task_new", label: "Neue Odoo-Aufgabe anlegen", icon: "fa-plus-square", keys: "aufgabe neu anlegen task", needsProject: true },
    { id: "template", label: "Vorlage einfügen …", icon: "fa-clone", keys: "vorlage template" },
    { id: "image", label: "Bild / Datei", icon: "fa-picture-o", keys: "bild datei image upload" },
    { id: "date", label: "Heutiges Datum", icon: "fa-calendar", keys: "datum heute date" },
];

// Sonder-Syntax (Seitenlinks, Erwähnungen, Boxen), die im WYSIWYG-Editor als nicht editierbarer "Chip" erscheint und
// beim Speichern 1:1 wieder als Markdown herauskommt.
const WIKILINK_RULE = /!?\[\[[^\[\]\n]+?\]\]/;
const MENTION_RULE = /\[@[^\]\n]+\]\(odoo:\/\/[a-z0-9_.]+\/\d+\)/;
const CALLOUT_RULE = /\[![a-zA-Z-]+(?:\|[^\]\n]*)?\][+-]?/;
// Layout-Boxen (technisch Callouts): Spalten, einzelne Spalte, zentrierter Bereich
const LAYOUT_TYPES = new Set(["columns", "col", "center"]);
export const COLUMN_RATIOS = [
    { value: "", label: "Gleich breit" },
    { value: "1-2", label: "Schmal | Breit" },
    { value: "2-1", label: "Breit | Schmal" },
    { value: "1-3", label: "Seitenleiste | Inhalt" },
];

/** "[!columns|1-2]-" -> { type: "columns", meta: "1-2", fold: "-" } */
export function parseCalloutMarker(text) {
    const m = /^\[!([a-zA-Z-]+)(?:\|([^\]]*))?\]([+-]?)/.exec(text || "");
    return m ? { type: m[1].toLowerCase(), meta: m[2] || "", fold: m[3] || "" } : { type: "note", meta: "", fold: "" };
}
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;

const CALLOUT_LABELS = {
    note: "Notiz", info: "Info", tip: "Tipp", warning: "Warnung", danger: "Achtung", success: "Erledigt",
    question: "Frage", example: "Beispiel", quote: "Zitat", important: "Wichtig", todo: "To-do",
};

// Auswahl im Chip-Menü einer Hinweisbox (Box-Typen)
export const CALLOUT_TYPES = [
    { type: "note", label: "Notiz", color: "#3b82f6", icon: "fa-pencil" },
    { type: "info", label: "Info", color: "#3b82f6", icon: "fa-info-circle" },
    { type: "tip", label: "Tipp", color: "#16a34a", icon: "fa-lightbulb-o" },
    { type: "important", label: "Wichtig", color: "#3b82f6", icon: "fa-exclamation-circle" },
    { type: "success", label: "Erledigt", color: "#16a34a", icon: "fa-check-circle" },
    { type: "question", label: "Frage", color: "#d97706", icon: "fa-question-circle" },
    { type: "warning", label: "Warnung", color: "#d97706", icon: "fa-exclamation-triangle" },
    { type: "danger", label: "Achtung", color: "#dc2626", icon: "fa-bolt" },
    { type: "example", label: "Beispiel", color: "#7c3aed", icon: "fa-list" },
    { type: "quote", label: "Zitat", color: "#6b7280", icon: "fa-quote-left" },
    { type: "todo", label: "To-do", color: "#0891b2", icon: "fa-check-square-o" },
];
export const CALLOUT_FOLD = [
    { value: "", label: "Immer offen" },
    { value: "+", label: "Einklappbar (offen)" },
    { value: "-", label: "Eingeklappt" },
];

/** Zerlegt [[Ziel#Abschnitt|Text]] bzw. ![[bild.png|300]]. */
export function parseWikilink(text) {
    const embed = text.startsWith("!");
    const inner = text.replace(/^!?\[\[/, "").replace(/\]\]$/, "");
    const pipe = inner.indexOf("|");
    const targetFull = pipe >= 0 ? inner.slice(0, pipe) : inner;
    const alias = pipe >= 0 ? inner.slice(pipe + 1) : "";
    const hash = targetFull.search(/[#^]/);
    return {
        embed,
        target: hash >= 0 ? targetFull.slice(0, hash) : targetFull,
        anchor: hash >= 0 ? targetFull.slice(hash + 1) : "",
        alias,
    };
}

export function buildWikilink({ embed, target, anchor, alias }) {
    const clean = (v) => (v || "").replace(/[[\]|]/g, "").trim();
    return `${embed ? "!" : ""}[[${clean(target)}${anchor ? "#" + clean(anchor) : ""}${alias ? "|" + clean(alias) : ""}]]`;
}

/**
 * Toast UI liefert Markdown mit eigenen Eigenheiten zurück. Hier wird es wieder
 * auf einheitlichen Stil gebracht (Widgets auspacken, "-" statt "*" für Listen, "---" als Trennlinie).
 */
// Unsichtbares Zeichen, mit dem Widgets in Tabellenzellen eingerahmt werden (siehe protectTableWidgets)
const ZWSP = "\u200B";
const TABLE_WIDGET_RE = new RegExp(`${WIKILINK_RULE.source}|${MENTION_RULE.source}`, "g");

/**
 * Toast UI verwirft Tabellenzellen, die mit einem Widget ([[Link]] / @-Erwähnung) beginnen oder enden.
 * Deshalb werden Widgets in Tabellenzeilen vor dem Laden mit einem Zero-Width-Space eingerahmt;
 * normalizeMarkdown entfernt die Zeichen beim Speichern wieder.
 */
export function protectTableWidgets(md) {
    let inFence = false;
    const lines = (md || "").split("\n");
    const out = [];
    lines.forEach((line, i) => {
        if (/^\s*(```|~~~)/.test(line)) {
            inFence = !inFence;
        } else if (!inFence && /^\s*\|/.test(line)) {
            line = line.replace(TABLE_WIDGET_RE, (m) => ZWSP + m + ZWSP);
        } else if (!inFence && /^(\s*>)+\s*$/.test(line) && /^(\s*>)+\s*#{1,6}\s/.test(lines[i + 1] || "")) {
            // Toast-UI-Fehler: Leerzeile in einer Box direkt vor einer Überschrift macht die Überschrift
            // zu Text ("\#\# …"). Eine Überschrift braucht keine Leerzeile davor -> beim Laden weglassen.
            return;
        }
        out.push(line);
    });
    return out.join("\n");
}

/** Toast lässt die Trennzeile zwischen zwei Spalten weg -> dann würden sie verschmelzen. */
function restoreNestedCalloutGaps(md) {
    const depth = (line) => (/^(\s*>)+/.exec(line)?.[0].match(/>/g) || []).length;
    const out = [];
    for (const line of md.split("\n")) {
        const d = depth(line);
        if (d >= 2 && new RegExp(`^(\\s*>){${d}}\\s*\\[![a-zA-Z-]+`).test(line) && out.length && depth(out.at(-1)) >= d) {
            out.push(">".repeat(d - 1).split("").join(" "));
        }
        out.push(line);
    }
    return out.join("\n");
}

export function normalizeMarkdown(md) {
    let inFence = false;
    md = restoreNestedCalloutGaps(md.replace(/\$\$widget\d+ ([\s\S]*?)\$\$/g, "$1"));
    return md
        .replace(/\$\$widget\d+ ([\s\S]*?)\$\$/g, "$1")
        .replaceAll(ZWSP, "")
        .split("\n")
        .map((line) => {
            if (/^\s*(```|~~~)/.test(line)) {
                inFence = !inFence;
                return line;
            }
            if (inFence) {
                return line;
            }
            if (/^\*\*\*$/.test(line)) {
                return "---";
            }
            return line.replace(/^(\s*(?:>\s*)*)\*(?= |$)/, "$1-");
        })
        .join("\n")
        .replace(/\n*$/, "\n");
}

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) {
        e.className = cls;
    }
    if (text !== undefined) {
        e.textContent = text;
    }
    return e;
}

export class MarkdownEditor extends Component {
    static template = "wissensmanagement.MarkdownEditor";
    static props = {
        content: String,
        path: String,
        index: Object,
        onChange: Function,
        onSave: Function,
        onOpenPage: { type: Function, optional: true },
        onOpenRecord: { type: Function, optional: true },
        templates: { type: Object, optional: true },
        mentionCategories: { type: Array, optional: true },
        hasProject: { type: Boolean, optional: true },
        // Live-Mitschreiben: { join, onReady(sync), onLocalChange(), onReset(), onError() }
        collab: { type: Object, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.bus = this.props.collab ? useService("bus_service") : null;
        this.userColor = userColor;
        this.hostRef = useRef("host");
        this.rootRef = useRef("root");
        this.fileInputRef = useRef("fileInput");
        this.state = useState({
            suggest: null, // { kind: "page"|"mention", items, active, from, to, top, left }
            source: false,
            sourceText: "",
            words: 0,
            chipMenu: null, // Menü für Hinweisbox / Seitenlink / Erwähnung, siehe openChipMenu
            peers: [], // wer gerade live mitschreibt (aus den Cursor-Meldungen)
        });
        this.CALLOUT_TYPES = CALLOUT_TYPES;
        this.CALLOUT_FOLD = CALLOUT_FOLD;
        this.COLUMN_RATIOS = COLUMN_RATIOS;
        this.mentionTimer = null;

        onMounted(() => this.mountEditor());
        onWillUnmount(() => {
            clearTimeout(this.mentionTimer);
            this.rootRef.el?.removeEventListener("keydown", this.onKeydownCapture, true);
            this.hostRef.el?.removeEventListener("contextmenu", this.onChipEvent, true);
            this.hostRef.el?.removeEventListener("click", this.onChipEvent, true);
            document.removeEventListener("mousedown", this.onDocMouseDown, true);
            this.sync?.destroy();
            this.editor?.destroy();
        });
    }

    // ------------------------------------------------------------------
    // Editor aufbauen
    // ------------------------------------------------------------------
    /** Ausgangstext: bei gemeinsamer Bearbeitung der Stand, mit dem die Sitzung begonnen hat. */
    get initialContent() {
        return this.props.collab ? this.props.collab.join.base : this.props.content;
    }

    mountEditor() {
        const button = (icon, title, onClick) => {
            const b = el("button", "o_wm_tui_btn");
            b.type = "button";
            b.innerHTML = `<i class="fa ${icon}"></i>`;
            b.addEventListener("mousedown", (ev) => {
                ev.preventDefault();
                onClick();
            });
            return { name: title, tooltip: title, el: b };
        };

        this.editor = new toastui.Editor({
            el: this.hostRef.el,
            height: "100%",
            initialEditType: "wysiwyg",
            initialValue: protectTableWidgets(this.initialContent),
            previewStyle: "vertical",
            hideModeSwitch: true,
            frontMatter: true,
            language: "de-DE",
            usageStatistics: false,
            autofocus: false,
            placeholder: "Einfach losschreiben … Tipp: [[ verlinkt eine Seite, @ erwähnt Kontakte, Angebote & Co.",
            toolbarItems: [
                // Reihenfolge = Wichtigkeit: Was nicht in die Breite passt, landet im „⋯“-Menü.
                ["heading", "bold", "italic"],
                ["ul", "ol", "task"],
                [
                    button("fa-book", "Andere Wissensseite verlinken", () => this.startTrigger("[[")),
                    button("fa-at", "Odoo-Datensatz erwähnen (Kontakt, Angebot …)", () => this.startTrigger("@")),
                    button("fa-picture-o", "Bild oder Datei einfügen", () => this.fileInputRef.el.click()),
                    button("fa-info-circle", "Hinweisbox einfügen", () => this.insertCallout()),
                    button("fa-align-center", "Zentrieren (an/aus)", () => this.toggleCenter()),
                    button("fa-columns", "Spalten einfügen (nebeneinander)", () => this.insertColumns(2)),
                ],
                ["table", "quote", "hr"],
                ["link", "strike", "code", "codeblock"],
            ],
            widgetRules: [
                { rule: WIKILINK_RULE, toDOM: (text) => this.wikilinkDOM(text) },
                { rule: MENTION_RULE, toDOM: (text) => this.mentionDOM(text) },
                { rule: CALLOUT_RULE, toDOM: (text) => this.calloutDOM(text) },
            ],
            hooks: {
                addImageBlobHook: (blob) => {
                    this.uploadFiles([blob]);
                    return false;
                },
            },
            events: {
                change: () => this.onEditorChange(),
                keyup: () => this.updateSuggest(),
            },
        });
        // Ausgangszustand merken: Nur echte Änderungen lösen ein Speichern aus,
        // reines Öffnen formatiert die Datei nie um.
        this.baseline = normalizeMarkdown(this.editor.getMarkdown());
        this.lastValue = this.baseline;
        this.updateWords(this.initialContent);
        if (this.props.collab) {
            this.startCollab();
        }
        this.onKeydownCapture = (ev) => this.onKeydown(ev);
        this.rootRef.el.addEventListener("keydown", this.onKeydownCapture, true);
        // Klick oder Rechtsklick auf einen Chip öffnet dessen Menü
        this.onChipEvent = (ev) => this.handleChipEvent(ev);
        this.hostRef.el.addEventListener("contextmenu", this.onChipEvent, true);
        this.hostRef.el.addEventListener("click", this.onChipEvent, true);
        this.onDocMouseDown = (ev) => {
            if (this.state.chipMenu && !ev.target.closest(".o_wm_chipmenu")) {
                this.state.chipMenu = null;
            }
        };
        document.addEventListener("mousedown", this.onDocMouseDown, true);
        this.editor.focus();
        this.editor.moveCursorToStart();
    }

    startCollab() {
        const c = this.props.collab;
        const sync = new CollabSync({
            editor: this.editor,
            join: c.join,
            path: this.props.path,
            orm: this.orm,
            bus: this.bus,
            userId: user.userId,
            onLocalChange: () => c.onLocalChange?.(),
            onReset: () => c.onReset?.(),
            onError: (e) => c.onError?.(e),
            onRemote: (mapping) => {
                // offene Menüs merken sich Positionen -> durch fremde Änderungen verschieben
                const sg = this.state.suggest;
                if (sg) {
                    sg.from = mapping.map(sg.from, -1);
                    sg.to = mapping.map(sg.to, 1);
                }
                if (this.state.chipMenu?.pos !== undefined) {
                    this.state.chipMenu.pos = mapping.map(this.state.chipMenu.pos);
                }
            },
            onPeers: (peers) => {
                this.state.peers = peers.map((p) => ({ id: p.user_id, name: p.name }));
            },
        });
        sync.onLocalChange = () => c.onLocalChange?.();
        if (!sync.start()) {
            c.onError?.(new Error("Schritte passen nicht zum Ausgangstext"));
            return;
        }
        this.sync = sync;
        // nach den Schritten der anderen den aktuellen Stand melden
        this.onEditorChange();
        c.onReady?.(sync, this);
    }

    /** Inhalt komplett ersetzen (z. B. nach dem Zusammenführen); wird live an alle verteilt. */
    setContent(md) {
        this.editor.setMarkdown(protectTableWidgets(md), false);
        this.onEditorChange();
    }

    getMarkdown() {
        return normalizeMarkdown(this.editor.getMarkdown());
    }

    onEditorChange() {
        const md = this.getMarkdown();
        if (md === this.lastValue) {
            return;
        }
        this.lastValue = md;
        this.updateWords(md);
        // unverändert gegenüber dem Laden -> Originaltext (inkl. Formatierung) behalten
        this.props.onChange(md === this.baseline ? this.initialContent : md);
    }

    updateWords(md) {
        this.state.words = (md.match(/[\p{L}\p{N}]+/gu) || []).length;
    }

    /** Quelltext-Ansicht mit echtem Markdown (ohne Toasts interne $$widget-Schreibweise). */
    toggleSource() {
        this.state.suggest = null;
        this.state.chipMenu = null;
        if (!this.state.source) {
            this.state.sourceText = this.getMarkdown();
            this.state.source = true;
        } else {
            this.state.source = false;
            this.editor.setMarkdown(protectTableWidgets(this.state.sourceText), false);
            this.onEditorChange();
        }
    }

    onSourceInput(ev) {
        this.state.sourceText = ev.target.value;
        this.lastValue = ev.target.value;
        this.updateWords(ev.target.value);
        this.props.onChange(ev.target.value === this.baseline ? this.initialContent : ev.target.value);
    }

    // ------------------------------------------------------------------
    // Chip-Menü: Hinweisbox-Art, Seitenlink-Ziel/Abschnitt/Text/Einbetten, Erwähnung
    // ------------------------------------------------------------------
    /** Findet den Widget-Knoten im Dokument, zu dem ein DOM-Element gehört. */
    widgetAt(domEl) {
        const view = this.editor?.wwEditor?.view;
        const span = domEl.closest(".tui-widget");
        if (!view || !span) {
            return null;
        }
        let found = null;
        view.state.doc.descendants((node, pos) => {
            if (found) {
                return false;
            }
            if (node.type.name === "widget" && view.nodeDOM(pos) === span) {
                found = { pos, size: node.nodeSize, text: node.textContent.replace(/^\$\$widget\d+ |\$\$$/g, "") };
            }
        });
        return found && { ...found, span };
    }

    handleChipEvent(ev) {
        if (this.state.source) {
            return;
        }
        const widget = this.widgetAt(ev.target);
        if (!widget) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        this.openChipMenu(widget);
    }

    async openChipMenu(widget) {
        const rootRect = this.rootRef.el.getBoundingClientRect();
        const rect = widget.span.getBoundingClientRect();
        const base = {
            pos: widget.pos,
            size: widget.size,
            top: rect.bottom - rootRect.top + 6,
            left: Math.max(8, Math.min(rect.left - rootRect.left, rootRect.width - 380)),
        };
        const text = widget.text;
        if (new RegExp(`^${CALLOUT_RULE.source}$`).test(text)) {
            const marker = parseCalloutMarker(text);
            if (LAYOUT_TYPES.has(marker.type)) {
                this.state.chipMenu = { ...base, kind: marker.type, meta: marker.meta };
                return;
            }
            this.state.chipMenu = { ...base, kind: "callout", type: marker.type, fold: marker.fold };
        } else if (new RegExp(`^${MENTION_RULE.source}$`).test(text)) {
            const m = /^\[@([^\]]+)\]\(odoo:\/\/([a-z0-9_.]+)\/(\d+)\)$/.exec(text);
            this.state.chipMenu = { ...base, kind: "mention", name: m[1], model: m[2], id: parseInt(m[3], 10) };
        } else {
            const link = parseWikilink(text);
            const resolved = link.target ? this.props.index.resolve(link.target, this.props.path) : this.props.path;
            const isImage = !!resolved && IMAGE_EXT.test(resolved);
            this.state.chipMenu = {
                ...base,
                kind: "wikilink",
                ...link,
                width: isImage && /^\d+$/.test(link.alias) ? link.alias : "",
                alias: isImage && /^\d+$/.test(link.alias) ? "" : link.alias,
                resolved,
                isImage,
                headings: [],
                pageQuery: "",
                pageResults: [],
            };
            this.loadHeadings();
        }
    }

    async loadHeadings() {
        const menu = this.state.chipMenu;
        if (!menu || menu.kind !== "wikilink") {
            return;
        }
        const path = menu.target ? this.props.index.resolve(menu.target, this.props.path) : this.props.path;
        menu.resolved = path;
        menu.headings = [];
        if (!path || !/\.md$/i.test(path)) {
            return;
        }
        try {
            const page = await this.orm.silent.call("wissen.vault", "read_page", [path]);
            let inFence = false;
            const headings = [];
            for (const line of page.content.split("\n")) {
                if (/^\s*(```|~~~)/.test(line)) {
                    inFence = !inFence;
                } else if (!inFence) {
                    const h = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
                    if (h) {
                        headings.push({ level: h[1].length, text: h[2] });
                    }
                }
            }
            if (this.state.chipMenu === menu) {
                menu.headings = headings;
            }
        } catch {
            // Seite nicht lesbar – Abschnittsauswahl bleibt leer
        }
    }

    /** Ersetzt den Chip durch neuen Widget-Text (oder durch normalen Text). */
    replaceChip(newText, { plain = false } = {}) {
        const menu = this.state.chipMenu;
        if (!menu) {
            return;
        }
        const view = this.editor.wwEditor.view;
        if (plain) {
            const tr = view.state.tr;
            view.dispatch(newText ? tr.replaceWith(menu.pos, menu.pos + menu.size, view.state.schema.text(newText))
                : tr.delete(menu.pos, menu.pos + menu.size));
        } else {
            this.editor.replaceWithWidget(menu.pos, menu.pos + menu.size, newText);
        }
        // Größe des neuen Knotens merken, damit weitere Änderungen im offenen Menü funktionieren
        const node = view.state.doc.nodeAt(menu.pos);
        menu.size = node ? node.nodeSize : menu.size;
        this.onEditorChange();
    }

    setCallout(type, fold) {
        const menu = this.state.chipMenu;
        menu.type = type ?? menu.type;
        menu.fold = fold ?? menu.fold;
        this.replaceChip(`[!${menu.type}]${menu.fold}`);
    }

    setColumnRatio(meta) {
        const m = this.state.chipMenu;
        m.meta = meta;
        this.replaceChip(`[!columns${meta ? "|" + meta : ""}]`);
    }

    chipAddColumn() {
        const box = this.findBox(this.state.chipMenu.pos, ["columns"]);
        this.state.chipMenu = null;
        if (box) {
            this.addColumn(box);
        }
    }

    chipRemoveColumn() {
        const pos = this.state.chipMenu.pos;
        this.state.chipMenu = null;
        this.removeColumn(pos);
    }

    chipUnwrap() {
        const m = this.state.chipMenu;
        this.state.chipMenu = null;
        const types = m.kind === "callout" ? null : [m.kind === "col" ? "columns" : m.kind];
        this.unwrapBox(m.pos, types);
        this.editor.focus();
    }

    applyWikilink() {
        const m = this.state.chipMenu;
        const alias = m.isImage ? (m.width || "").replace(/\D/g, "") : m.alias;
        this.replaceChip(buildWikilink({ embed: m.embed, target: m.target, anchor: m.anchor, alias }));
        this.state.chipMenu = null;
        this.editor.focus();
    }

    stripMd(path) {
        return (path || "").replace(/\.md$/i, "");
    }

    searchChipPage(ev) {
        const m = this.state.chipMenu;
        m.pageQuery = ev.target.value;
        const q = m.pageQuery.toLowerCase();
        m.pageResults = q
            ? this.props.index.pages().filter((p) => p.toLowerCase().includes(q)).slice(0, 8)
            : [];
    }

    chooseChipPage(path) {
        const m = this.state.chipMenu;
        m.target = this.shortestLink(path);
        m.anchor = "";
        m.pageQuery = "";
        m.pageResults = [];
        this.loadHeadings();
    }

    chipToPlainText() {
        const m = this.state.chipMenu;
        const text = m.kind === "mention" ? m.name : m.kind === "wikilink" ? (m.alias || basename(m.target, false) || m.anchor) : "";
        this.replaceChip(text, { plain: true });
        this.state.chipMenu = null;
        this.editor.focus();
    }

    chipReplaceMention() {
        // Erwähnung durch "@" ersetzen und Auswahlmenü öffnen
        const m = this.state.chipMenu;
        this.replaceChip("@", { plain: true });
        this.state.chipMenu = null;
        this.editor.focus();
        this.editor.setSelection(m.pos + 1, m.pos + 1);
        this.updateSuggest();
    }

    chipOpen() {
        const m = this.state.chipMenu;
        this.state.chipMenu = null;
        if (m.kind === "mention") {
            this.props.onOpenRecord?.(m.model, m.id);
        } else if (m.kind === "wikilink" && m.resolved) {
            if (/\.md$/i.test(m.resolved)) {
                this.props.onOpenPage?.(m.resolved, m.anchor ? slugify(m.anchor) : null);
            } else {
                window.open(fileUrl(m.resolved), "_blank");
            }
        }
    }

    // ------------------------------------------------------------------
    // Darstellung der Sonder-Elemente im Editor
    // ------------------------------------------------------------------
    wikilinkDOM(text) {
        const embed = text.startsWith("!");
        const inner = text.replace(/^!?\[\[/, "").replace(/\]\]$/, "");
        const [targetFull, alias] = inner.split("|");
        const target = targetFull.split(/[#^]/)[0];
        const resolved = target ? this.props.index.resolve(target, this.props.path) : this.props.path;
        if (embed && resolved && IMAGE_EXT.test(resolved)) {
            const img = el("img", "o_wm_editor_img");
            img.src = fileUrl(resolved);
            img.alt = basename(resolved, false);
            if (/^\d+$/.test(alias || "")) {
                img.width = parseInt(alias, 10);
            }
            return img;
        }
        const chip = el("span", "o_wm_chip o_wm_chip_page" + (resolved ? "" : " o_wm_chip_missing"));
        chip.title = resolved ? resolved.replace(/\.md$/i, "") : "Seite existiert noch nicht";
        chip.append(el("i", `fa ${embed ? "fa-paperclip" : "fa-file-text-o"} me-1`));
        chip.append(alias && !/^\d+$/.test(alias) ? alias : basename(targetFull.replace(/#/, " › "), false));
        return chip;
    }

    mentionDOM(text) {
        const m = /^\[@([^\]]+)\]\(odoo:\/\/([a-z0-9_.]+)\/(\d+)\)$/.exec(text);
        const chip = el("span", "o_wm_chip o_wm_chip_mention o_wm_mention", "@" + (m ? m[1] : text));
        if (m) {
            chip.dataset.wmModel = m[2];
            chip.dataset.wmId = m[3];
            // Avatar, Phase & Hover-Vorschau nachladen, sobald der Chip im Dokument hängt
            setTimeout(() => decorateMention(this.orm, chip, { allowBlock: false }), 0);
        }
        return chip;
    }

    calloutDOM(text) {
        const { type, meta } = parseCalloutMarker(text);
        if (LAYOUT_TYPES.has(type)) {
            const label = { columns: "Spalten", col: "Spalte", center: "Zentriert" }[type];
            const icon = { columns: "fa-columns", col: "fa-square-o", center: "fa-align-center" }[type];
            const chip = el("span", `o_wm_chip o_wm_chip_layout o_wm_layout_${type}${meta ? " o_wm_cols_" + meta : ""}`);
            chip.title = "Klicken für Optionen";
            chip.append(el("i", `fa ${icon} me-1`), label);
            return chip;
        }
        const chip = el("span", `o_wm_chip o_wm_chip_callout o_wm_callout_${type}`);
        chip.title = "Hinweisbox – klicken für Optionen";
        chip.append(el("i", "fa fa-info-circle me-1"), CALLOUT_LABELS[type] || type);
        return chip;
    }

    // ------------------------------------------------------------------
    // Boxen: Hinweisbox, Zentriert, Spalten (ProseMirror-Ebene)
    // ------------------------------------------------------------------
    get pm() {
        const view = this.editor.wwEditor.view;
        return { view, schema: view.state.schema };
    }

    widgetNode(text) {
        // Callout-Marker sind Widget-Regel Nr. 2 (siehe widgetRules)
        const { schema } = this.pm;
        return schema.nodes.widget.create({ info: "widget2" }, schema.text(`$$widget2 ${text}$$`));
    }

    /** Umschließt den aktuellen Absatz/Markierung mit einer Box und setzt den Marker als erste Zeile. */
    wrapInBox(marker) {
        if (this.state.source) {
            return;
        }
        this.editor.focus();
        this.editor.exec("blockQuote");
        const { view, schema } = this.pm;
        const $from = view.state.selection.$from;
        for (let d = $from.depth; d > 0; d--) {
            if ($from.node(d).type.name === "blockQuote") {
                const pos = $from.before(d) + 1;
                view.dispatch(view.state.tr.insert(pos, schema.nodes.paragraph.create(null, this.widgetNode(marker))));
                return;
            }
        }
    }

    insertCallout() {
        this.wrapInBox("[!note]");
    }

    /** Box um die Cursor-Position, deren erste Zeile ein bestimmter Marker-Typ ist. */
    findBox(pos, types) {
        const { view } = this.pm;
        const $pos = view.state.doc.resolve(pos);
        for (let d = $pos.depth; d > 0; d--) {
            const node = $pos.node(d);
            if (node.type.name === "blockQuote") {
                const first = node.firstChild;
                const markerText = first?.firstChild?.type.name === "widget" ? first.firstChild.textContent.replace(/^\$\$widget\d+ |\$\$$/g, "") : "";
                const marker = parseCalloutMarker(markerText);
                if (markerText && (!types || types.includes(marker.type))) {
                    return { node, start: $pos.before(d), end: $pos.before(d) + node.nodeSize, marker, depth: d };
                }
            }
        }
        return null;
    }

    /** Inhalt einer Box ohne Marker-Zeile (Titeltext der Marker-Zeile bleibt als Absatz erhalten). */
    boxContent(box) {
        const { schema } = this.pm;
        const nodes = [];
        box.node.forEach((child, _offset, i) => {
            if (i === 0 && child.type.name === "paragraph") {
                const rest = [];
                child.forEach((inline, _o, j) => {
                    if (j > 0) {
                        rest.push(inline);
                    }
                });
                const restText = rest.map((n) => n.textContent).join("").trim();
                if (restText) {
                    nodes.push(schema.nodes.paragraph.create(null, rest));
                }
            } else if (box.marker.type === "columns" && child.type.name === "blockQuote") {
                this.boxContent({ node: child, marker: { type: "col" } }).forEach((n) => nodes.push(n));
            } else {
                nodes.push(child);
            }
        });
        return nodes;
    }

    unwrapBox(pos, types) {
        const box = this.findBox(pos, types);
        if (!box) {
            return;
        }
        const { view, schema } = this.pm;
        const content = this.boxContent(box);
        view.dispatch(view.state.tr.replaceWith(box.start, box.end, content.length ? content : [schema.nodes.paragraph.create()]));
        this.onEditorChange();
    }

    toggleCenter() {
        if (this.state.source) {
            return;
        }
        const { view } = this.pm;
        const box = this.findBox(view.state.selection.from, ["center"]);
        if (box) {
            this.unwrapBox(view.state.selection.from, ["center"]);
        } else {
            this.wrapInBox("[!center]");
        }
    }

    insertColumns(count, from = null) {
        // Spalten immer als eigener Block auf oberster Ebene einfügen: Innerhalb einer anderen
        // Box (z. B. Zentriert) würde Toast diese sonst in zwei Teile zerschneiden.
        const { view, schema } = this.pm;
        const { blockQuote, paragraph } = schema.nodes;
        let tr = view.state.tr;
        const $pos = tr.doc.resolve(Math.min(from ?? view.state.selection.from, tr.doc.content.size));
        const block = $pos.depth >= 1 ? $pos.node($pos.depth) : null;
        let insertAt = $pos.depth >= 1 ? $pos.after(1) : $pos.pos;
        if (block && block.isTextblock && !block.textContent.trim()) {
            // leere Zeile (z. B. nach „/2 spalten“) entfernen bzw. bei oberster Ebene ersetzen
            const start = $pos.before($pos.depth);
            const parentCount = $pos.node($pos.depth - 1).childCount;
            if ($pos.depth === 1 || parentCount > 1) {
                tr = tr.delete(start, $pos.after($pos.depth));
                insertAt = $pos.depth === 1 ? start : tr.mapping.map(insertAt);
            }
        }
        const cols = Array.from({ length: count }, (_v, i) => blockQuote.create(null, [
            paragraph.create(null, this.widgetNode("[!col]")),
            paragraph.create(null, schema.text(`Spalte ${i + 1}`)),
        ]));
        const box = blockQuote.create(null, [paragraph.create(null, this.widgetNode("[!columns]")), ...cols]);
        tr = tr.insert(insertAt, box);
        view.dispatch(tr.scrollIntoView());
        this.onEditorChange();
    }

    addColumn(box) {
        const { view, schema } = this.pm;
        const col = schema.nodes.blockQuote.create(null, [
            schema.nodes.paragraph.create(null, this.widgetNode("[!col]")),
            schema.nodes.paragraph.create(null, schema.text("Neue Spalte")),
        ]);
        view.dispatch(view.state.tr.insert(box.end - 1, col));
        this.onEditorChange();
    }

    removeColumn(pos) {
        const col = this.findBox(pos, ["col"]);
        if (!col) {
            return;
        }
        const { view } = this.pm;
        const parent = this.findBox(col.start, ["columns"]);
        if (parent && parent.node.childCount <= 2) {
            // letzte Spalte -> ganzes Spaltenlayout auflösen
            return this.unwrapBox(col.start, ["columns"]);
        }
        view.dispatch(view.state.tr.delete(col.start, col.end));
        this.onEditorChange();
    }

    // ------------------------------------------------------------------
    // Autovervollständigung für [[ und @
    // ------------------------------------------------------------------
    startTrigger(trigger) {
        this.editor.focus();
        const [from] = this.editor.getSelection();
        let before = "";
        try {
            before = from > 1 ? this.editor.getSelectedText(from - 1, from) : "";
        } catch {
            before = "";
        }
        this.editor.insertText(trigger === "@" && before && !/\s/.test(before) ? " @" : trigger);
        this.updateSuggest();
    }

    textBeforeCursor() {
        const [from] = this.editor.getSelection();
        if (typeof from !== "number") {
            return { text: "", from: 0 };
        }
        let text = "";
        try {
            text = this.editor.getSelectedText(Math.max(1, from - 60), from);
        } catch {
            text = "";
        }
        return { text, from };
    }

    updateSuggest() {
        if (!this.editor || this.state.source) {
            return;
        }
        const { text, from } = this.textBeforeCursor();
        const slash = /(?:^|[\s\n])\/([\p{L}\p{N} ]{0,24})$/u.exec(text);
        if (slash && !/\s{2}$/.test(slash[1])) {
            const q = slash[1].trim().toLowerCase();
            const items = SLASH_COMMANDS.filter((c) => (!c.needsProject || this.props.hasProject))
                .filter((c) => !q || c.label.toLowerCase().includes(q) || c.keys.includes(q))
                .map((c) => ({ label: c.label, sub: "", value: { ...c } }));
            if (items.length) {
                return this.openSuggest("slash", items, from - slash[1].length - 1, from);
            }
        }
        const wiki = /\[\[([^\[\]\n|#]*)$/.exec(text);
        const mention = !wiki && /(?:^|[\s(])@([\p{L}\p{N} ._\-&]{0,40})$/u.exec(text);
        if (wiki) {
            const q = wiki[1].toLowerCase();
            const items = this.props.index
                .pages()
                .map((p) => ({ path: p, name: basename(p), q: p.toLowerCase() }))
                .filter((p) => !q || p.q.includes(q))
                .sort((a, b) => {
                    const as = a.name.toLowerCase().startsWith(q) ? 0 : 1;
                    const bs = b.name.toLowerCase().startsWith(q) ? 0 : 1;
                    return as - bs || a.name.length - b.name.length;
                })
                .slice(0, 12)
                .map((p) => ({ label: p.name, sub: p.path.replace(/\.md$/i, ""), value: p }));
            if (q && !items.some((i) => i.label.toLowerCase() === q)) {
                items.push({ label: wiki[1], sub: "Link auf neue Seite", value: { name: wiki[1], create: true } });
            }
            this.openSuggest("page", items, from - wiki[1].length - 2, from);
        } else if (mention) {
            const term = mention[1];
            const start = from - term.length - 1;
            clearTimeout(this.mentionTimer);
            // Nur "@": Bereiche zur Auswahl anbieten. "@auf": passende Bereiche oben, Treffer darunter.
            const catItems = this.categoryItems(term);
            if (!term) {
                this.openSuggest("mention", catItems, start, from);
                return;
            }
            if (catItems.length) {
                this.openSuggest("mention", catItems, start, from);
            }
            this.mentionTimer = setTimeout(async () => {
                const res = await this.orm.call("wissen.vault", "search_mentions", [term]);
                const [now] = this.editor.getSelection();
                if (now === from) {
                    const items = res.map((r) => ({
                        label: r.name,
                        sub: r.context ? `${r.model_label} · ${r.context}` : r.model_label,
                        value: r,
                    }));
                    this.openSuggest("mention", [...catItems, ...items], start, from);
                }
            }, 200);
        } else {
            this.state.suggest = null;
        }
    }

    /** Einträge für das @-Menü: ein Eintrag pro Bereich ("@kontakt · @kunde …" / "Kontakte"). */
    categoryItems(term) {
        const q = (term || "").toLowerCase();
        if (q.includes(" ")) {
            return []; // Bereich ist schon gewählt ("@aufgabe bank")
        }
        return (this.props.mentionCategories || [])
            .filter((c) => c.aliases.length && (!q || c.aliases.some((a) => a.startsWith(q))))
            .map((c) => ({
                label: c.aliases.map((a) => "@" + a).join(" · "),
                sub: c.label,
                value: { category: true, alias: c.aliases.find((a) => a.startsWith(q)) || c.aliases[0], icon: c.icon },
            }));
    }

    openSuggest(kind, items, from, to) {
        if (!items.length) {
            this.state.suggest = null;
            return;
        }
        const sel = window.getSelection();
        const rootRect = this.rootRef.el.getBoundingClientRect();
        let top = 80;
        let left = 40;
        if (sel && sel.rangeCount) {
            const rect = sel.getRangeAt(0).getBoundingClientRect();
            if (rect.top || rect.left) {
                top = rect.bottom - rootRect.top + 4;
                // unten zu wenig Platz -> Liste oberhalb des Cursors öffnen
                if (top + 330 > rootRect.height) {
                    top = Math.max(8, rect.top - rootRect.top - 330);
                }
                left = Math.min(rect.left - rootRect.left, rootRect.width - 360);
            }
        }
        const prev = this.state.suggest;
        this.state.suggest = {
            kind,
            items,
            from,
            to,
            top,
            left: Math.max(8, left),
            active: prev && prev.kind === kind ? Math.min(prev.active, items.length - 1) : 0,
        };
    }

    applySuggestion(item) {
        const s = this.state.suggest;
        if (!s) {
            return;
        }
        if (s.kind === "slash") {
            this.state.suggest = null;
            this.editor.replaceSelection("", s.from, s.to);
            return this.runSlash(item.value.id, s.from);
        }
        if (s.kind === "template") {
            this.state.suggest = null;
            return this.insertTemplate(item.value.path, s.from, s.to);
        }
        if (s.kind === "mention" && item.value.category) {
            // Bereich gewählt: "@aufgabe " einsetzen und sofort die Datensätze dieses Bereichs zeigen
            this.state.suggest = null;
            this.editor.replaceSelection(`@${item.value.alias} `, s.from, s.to);
            this.editor.focus();
            return this.updateSuggest();
        }
                let text;
        if (s.kind === "page") {
            const name = item.value.create ? item.value.name : this.shortestLink(item.value.path);
            text = `[[${name}]]`;
        } else {
            const r = item.value;
            text = `[@${r.name.replace(/[[\]]/g, "")}](odoo://${r.model}/${r.id})`;
        }
        this.state.suggest = null;
        // ein evtl. schon vorhandenes "]]" hinter dem Cursor mit ersetzen
        let to = s.to;
        try {
            if (s.kind === "page" && this.editor.getSelectedText(s.to, s.to + 2) === "]]") {
                to += 2;
            }
        } catch {
            // Dokumentende
        }
        this.editor.replaceWithWidget(s.from, to, text);
        this.editor.insertText(" ");
        this.editor.focus();
    }

    async runSlash(id, pos) {
        const ed = this.editor;
        ed.focus();
        switch (id) {
            case "h1":
            case "h2":
            case "h3":
                return ed.exec("heading", { level: parseInt(id[1], 10) });
            case "ul": return ed.exec("bulletList");
            case "ol": return ed.exec("orderedList");
            case "task": return ed.exec("taskList");
            case "table": return ed.exec("addTable", { rowCount: 3, columnCount: 3 });
            case "hr": return ed.exec("hr");
            case "quote": return ed.exec("blockQuote");
            case "callout": return this.insertCallout();
            case "cols2": return this.insertColumns(2, pos);
            case "cols3": return this.insertColumns(3, pos);
            case "center": return this.toggleCenter();
            case "code": return ed.exec("codeBlock");
            case "mermaid":
                return this.insertMarkdownAt(pos, "```mermaid\nflowchart LR\n    A[Start] --> B{Entscheidung}\n    B -->|Ja| C[Weiter]\n    B -->|Nein| D[Stopp]\n```");
            case "page": return this.startTrigger("[[");
            case "mention": return this.startTrigger("@");
            case "task_mention":
                ed.insertText("@aufgabe ");
                return this.updateSuggest();
            case "task_new": return this.createTask(pos);
            case "template": return this.chooseTemplate(pos);
            case "image": return this.fileInputRef.el.click();
            case "date":
                return ed.insertText(new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }));
        }
    }

    createTask(pos) {
        this.dialog.add(PromptDialog, {
            title: "Neue Odoo-Aufgabe",
            label: "Titel der Aufgabe",
            confirmLabel: "Anlegen & verlinken",
            onConfirm: async (name) => {
                const [id] = await this.orm.create("project.task", [{ name }]);
                this.editor.focus();
                this.editor.replaceWithWidget(pos, pos, `[@${name.replace(/[[\]]/g, "")}](odoo://project.task/${id})`);
                this.editor.insertText(" ");
                this.notification.add("Aufgabe angelegt – im Projekt-Modul unter „Meine Aufgaben“.", { type: "success" });
            },
        });
    }

    async chooseTemplate(pos) {
        if (!this.props.templates) {
            return;
        }
        const list = await this.props.templates.list();
        if (!list.length) {
            this.notification.add("Noch keine Vorlagen – lege Seiten im Vorlagen-Ordner an.", { type: "info" });
            return;
        }
        const items = list.map((t) => ({ label: t.name, sub: "Vorlage", value: t }));
        this.openSuggest("template", items, pos, pos);
    }

    async insertTemplate(path, from, to) {
        const content = await this.props.templates.content(path);
        await this.insertMarkdownAt(from, content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, ""), to);
        this.notification.add("Vorlage eingefügt", { type: "success" });
    }

    /** Fügt Markdown an einer Stelle ein (über eine Markierung im Text, da der WYSIWYG-Editor nur Text einfügen kann). */
    async insertMarkdownAt(from, markdown, to = from) {
        const MARK = "WMINSERTMARK" + Date.now();
        this.editor.replaceSelection(MARK, from, to);
        const lines = normalizeMarkdown(this.editor.getMarkdown()).split("\n");
        const i = lines.findIndex((l) => l.includes(MARK));
        const block = ["", ...markdown.trim().split("\n"), ""];
        if (i < 0) {
            lines.push(...block);
        } else if (lines[i].replace(MARK, "").trim()) {
            // Cursor stand mitten in einer Zeile: Block dahinter einfügen, Zeile unverändert lassen
            lines[i] = lines[i].replace(MARK, "");
            lines.splice(i + 1, 0, ...block);
        } else {
            lines.splice(i, 1, ...block);
        }
        const next = lines.join("\n").replace(/\n{3,}/g, "\n\n");
        this.editor.setMarkdown(protectTableWidgets(next), false);
        this.onEditorChange();
    }

    /** Kurzform: nur der Seitenname, außer er ist nicht eindeutig. */
    shortestLink(path) {
        const name = basename(path);
        const dupes = this.props.index.pages().filter((p) => basename(p).toLowerCase() === name.toLowerCase());
        return dupes.length > 1 ? path.replace(/\.md$/i, "") : name;
    }

    onKeydown(ev) {
        // Live-Mitschreiben: Klick-Position sofort übernehmen, bevor die Taste verarbeitet wird
        this.sync?.flushInput();
        if (this.state.chipMenu && ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.state.chipMenu = null;
            return;
        }
        const s = this.state.suggest;
        if (s && s.items.length && ["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"].includes(ev.key)) {
            ev.preventDefault();
            ev.stopPropagation();
            if (ev.key === "Escape") {
                this.state.suggest = null;
            } else if (ev.key === "Enter" || ev.key === "Tab") {
                this.applySuggestion(s.items[s.active]);
            } else {
                const d = ev.key === "ArrowDown" ? 1 : -1;
                s.active = (s.active + d + s.items.length) % s.items.length;
            }
            return;
        }
        const mod = ev.ctrlKey || ev.metaKey;
        if (mod && ev.key.toLowerCase() === "s") {
            ev.preventDefault();
            ev.stopPropagation();
            this.props.onSave();
        } else if (mod && ev.key.toLowerCase() === "k") {
            ev.stopPropagation(); // nicht die Odoo-Befehlspalette öffnen
        }
    }

    // ------------------------------------------------------------------
    // Bilder & Dateien
    // ------------------------------------------------------------------
    onFileChosen(ev) {
        this.uploadFiles([...ev.target.files]);
        ev.target.value = "";
    }

    onDrop(ev) {
        // Bilder behandelt Toast UI selbst (addImageBlobHook), andere Dateien wir
        const files = [...(ev.dataTransfer?.files || [])].filter((f) => !f.type.startsWith("image/"));
        if (files.length) {
            ev.preventDefault();
            ev.stopPropagation();
            this.uploadFiles(files);
        }
    }

    async uploadFiles(files) {
        for (const file of files) {
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(",")[1]);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
            let name = file.name;
            if (!name || name === "image.png" || name === "blob") {
                const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
                name = `Eingefügt ${ts}.${(file.type.split("/")[1] || "png").replace("jpeg", "jpg")}`;
            }
            const path = await this.orm.call("wissen.vault", "upload_file", [this.props.path, name, data]);
            this.props.index.add(path);
            const [from, to] = this.editor.getSelection();
            this.editor.replaceWithWidget(from, to, `![[${basename(path, false)}]]`);
        }
        this.notification.add(files.length > 1 ? `${files.length} Dateien eingefügt` : "Datei eingefügt", {
            type: "success",
        });
    }
}
