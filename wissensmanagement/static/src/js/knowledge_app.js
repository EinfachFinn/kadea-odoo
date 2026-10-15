/* global luxon */
import { Component, markup, onMounted, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { LinkIndex, basename, dirname, renderMarkdown, toggleTask, escapeHtml } from "./markdown";
import { MarkdownEditor } from "./editor";
import { TreeNode } from "./tree_node";
import { decorateMention, invalidateCards } from "./mention_cards";
import { ConflictDialog, CreatePageDialog, MoveDialog, PromptDialog } from "./dialogs";
import { ChatterDialog, HistoryDialog, ShareDialog, TaskDialog, TrashDialog, TRASH_DAYS, absTime, mergeThreeWay, relTime } from "./collab";
import { CommentsPanel } from "./comments";
import { VaultOverview } from "./overview";
import { decorateReadView, highlightQuotes } from "./read_view";
import { router } from "@web/core/browser/router";

const LS = {
    get(key, fallback) {
        try {
            const v = localStorage.getItem("wm_" + key);
            return v === null ? fallback : JSON.parse(v);
        } catch {
            return fallback;
        }
    },
    set(key, value) {
        try {
            localStorage.setItem("wm_" + key, JSON.stringify(value));
        } catch {
            // Speicher voll / privat: nur Komfortfunktion
        }
    },
};

const AUTOSAVE_MS = 1500;
const HEARTBEAT_MS = 15000;

export class KnowledgeApp extends Component {
    static template = "wissensmanagement.App";
    static components = { MarkdownEditor, TreeNode, Dropdown, DropdownItem, CommentsPanel, VaultOverview };
    static props = { ...standardActionServiceProps };

    setup() {
        this.collabCallbacks = this.makeCollabCallbacks();
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.contentRef = useRef("content");
        this.searchRef = useRef("search");
        this.htmlRef = useRef("html");
        this.config = null;
        this.index = new LinkIndex([]);
        this.state = useState({
            loading: true,
            error: null,
            tree: [],
            rootName: "",
            expanded: LS.get("expanded", {}),
            currentPath: null,
            page: null,
            html: "",
            headings: [],
            backlinks: [],
            mode: "read",
            dirty: false,
            saving: false,
            lastSaved: null,
            query: "",
            results: null,
            dragging: null,
            dropTarget: null,
            sidebarOpen: LS.get("sidebar", true),
            panelOpen: LS.get("panel", true),
            showTitle: true,
            editorKey: 0,
            collabJoin: null, // Live-Mitschreiben: Antwort von collab_join für die offene Seite
            collabOff: false, // Abgleich nicht möglich -> Editor arbeitet wie früher allein
            dropPos: null,
            sidebar: { favorites: [], recent: [] },
            sections: LS.get("sections", { favorites: true, recent: true }),
            others: [],
            panelTab: "outline",
            commentCount: 0,
            commentQuotes: [],
            pendingQuote: null,
            selectionBtn: null,
            overview: false,
        });
        this.relTime = relTime;
        this.absTime = absTime;
        this.draft = null;
        this.autosaveTimer = null;
        this.searchTimer = null;

        onWillStart(async () => {
            try {
                this.config = await this.orm.call("wissen.vault", "get_config", []);
            } catch (e) {
                this.state.error = e.data?.message || String(e);
                this.state.loading = false;
                return;
            }
            this.state.mode = this.canWrite ? LS.get("mode", this.config.default_mode) : "read";
            if (!this.config.configured) {
                this.state.loading = false;
                return;
            }
            await this.loadTree();
            this.loadSidebar();
            const ctxPath = this.props.action?.context?.wm_path || router.current.wm_path;
            const start = ctxPath || LS.get("last", null) || this.firstPage();
            if (start) {
                await this.openPage(start, { silent: true });
            }
            this.state.loading = false;
        });

        useEffect(
            () => this.decorateContent(),
            () => [this.state.html, this.state.mode, this.state.overview, this.state.page]
        );

        this.onKeydown = (ev) => {
            const active = document.activeElement;
            const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(active?.tagName) || active?.isContentEditable;
            if (ev.key === "/" && !typing && this.searchRef.el) {
                ev.preventDefault();
                this.searchRef.el.focus();
            } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "e" && this.canWrite) {
                ev.preventDefault();
                this.toggleMode();
            }
        };
        this.onBeforeUnload = (ev) => {
            if (this.state.dirty) {
                ev.preventDefault();
                ev.returnValue = "";
            }
        };
        onMounted(() => {
            window.addEventListener("keydown", this.onKeydown);
            window.addEventListener("beforeunload", this.onBeforeUnload);
            this.heartbeatTimer = setInterval(() => this.heartbeat(), HEARTBEAT_MS);
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this.onKeydown);
            window.removeEventListener("beforeunload", this.onBeforeUnload);
            clearInterval(this.heartbeatTimer);
            this.orm.silent.call("wissen.vault", "leave_page", []).catch(() => {});
            clearTimeout(this.autosaveTimer);
            if (this.state.dirty) {
                this.save();
            }
        });
    }

    // ------------------------------------------------------------------
    // Getter
    // ------------------------------------------------------------------
    get canWrite() {
        return !!this.config?.can_write;
    }

    get styleVars() {
        const s = this.config?.style || {};
        return [
            `--wm-accent: ${s.accent_color || "#714B67"}`,
            `--wm-wikilink: ${s.wikilink_color || "#7c3aed"}`,
            `--wm-mention: ${s.mention_color || "#0e7490"}`,
            `--wm-width: ${s.content_width || 860}px`,
            `--wm-font-size: ${s.font_size || 16}px`,
        ].join("; ");
    }

    get customCss() {
        // Nur Administratoren können das CSS setzen; "</" verhindert das Ausbrechen aus dem <style>-Tag.
        return (this.config?.style?.custom_css || "").replace(/<\//g, "");
    }

    get presetClass() {
        return `o_wm_preset_${this.config?.style?.preset || "default"}`;
    }

    get saveStatus() {
        if (this.state.saving) {
            return "Speichert …";
        }
        if (this.state.dirty) {
            return "Ungespeichert";
        }
        return this.state.lastSaved ? "Gespeichert" : "";
    }

    firstPage() {
        const home = this.state.tree.find((n) => n.is_page && /^(start|home|index|readme|willkommen)$/i.test(n.name));
        if (home) {
            return home.path;
        }
        const find = (nodes) => {
            for (const n of nodes) {
                if (n.is_page) {
                    return n.path;
                }
            }
            return nodes[0]?.path || null;
        };
        return find(this.state.tree);
    }

    // ------------------------------------------------------------------
    // Laden & Navigieren
    // ------------------------------------------------------------------
    async loadTree() {
        const res = await this.orm.call("wissen.vault", "get_tree", []);
        this.state.tree = res.tree;
        this.state.rootName = res.root_name;
        this.index.rebuild(res.files);
    }

    async openPage(path, { silent = false, anchor = null, keepScroll = false } = {}) {
        if (path === null || path === undefined) {
            return;
        }
        if (this.state.dirty) {
            await this.save();
        }
        let page;
        try {
            page = await this.orm.call("wissen.vault", "read_page", [path]);
        } catch (e) {
            if (silent) {
                LS.set("last", null);
                return;
            }
            throw e;
        }
        this.state.page = page;
        this.state.collabJoin = null;
        this.state.collabOff = false;
        this.sync = null;
        this.localDirty = false;
        this.state.editorKey++;
        this.state.overview = false;
        this.state.others = [];
        this.state.selectionBtn = null;
        this.state.pendingQuote = null;
        this.state.commentQuotes = [];
        this.state.currentPath = page.path;
        router.pushState({ wm_path: page.path });
        this.state.commentCount = page.meta?.comment_count || 0;
        this.heartbeat();
        if (page.is_page && page.meta?.comment_count) {
            this.orm.silent.call("wissen.vault", "list_comments", [page.path]).then((comments) => {
                if (this.state.page === page) {
                    const quotes = comments.filter((c) => !c.parent_id && !c.resolved && c.quote).map((c) => c.quote);
                    this.onCommentQuotes(quotes);
                }
            });
        }
        this.draft = page.content;
        this.state.dirty = false;
        this.state.lastSaved = null;
        this.state.results = null;
        this.expandTo(page.path);
        LS.set("last", page.path);
        this.render_();
        this.loadBacklinks(page.path);
        if (this.state.mode === "write") {
            await this.joinCollab();
        }
        if (anchor) {
            setTimeout(() => this.scrollTo(anchor), 50);
        } else if (this.contentRef.el && !keepScroll) {
            this.contentRef.el.scrollTop = 0;
        }
    }

    render_() {
        const page = this.state.page;
        if (!page?.is_page) {
            this.state.html = "";
            this.state.headings = [];
            return;
        }
        let { html, headings } = renderMarkdown(this.draft ?? page.content, { index: this.index, path: page.path });
        // Beginnt die Seite selbst mit "# <Seitenname>", wird diese Überschrift zum Seitentitel
        // (so steht "zuletzt bearbeitet von" direkt darunter und der Titel erscheint nicht doppelt)
        const h1 = headings[0];
        const startsWithH1 = /^(---[\s\S]*?---\s*)?#\s/.test((this.draft ?? page.content).trimStart());
        if (startsWithH1 && h1 && h1.level === 1 && h1.text.trim() === page.name.trim()) {
            html = html.replace(/<h1 [^>]*>[\s\S]*?<\/h1>\n?/, "");
        }
        this.state.html = markup(html);
        this.state.headings = headings;
        this.state.showTitle = true;
    }

    async loadBacklinks(path) {
        this.state.backlinks = [];
        if (!this.state.page?.is_page) {
            return;
        }
        const links = await this.orm.call("wissen.vault", "backlinks", [path]);
        if (this.state.currentPath === path) {
            this.state.backlinks = links;
        }
    }

    expandTo(path) {
        const parts = path.replace(/\.md$/i, "").split("/");
        const expanded = { ...this.state.expanded };
        // alle Vorfahren (als Ordner- oder Seitenpfad) aufklappen
        for (let i = 1; i < parts.length; i++) {
            const p = parts.slice(0, i).join("/");
            expanded[p] = true;
            expanded[p + ".md"] = true;
        }
        this.state.expanded = expanded;
    }

    toggleExpanded(path) {
        this.state.expanded[path] = !this.state.expanded[path];
        LS.set("expanded", this.state.expanded);
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
        LS.set("sidebar", this.state.sidebarOpen);
    }

    togglePanel() {
        this.state.panelOpen = !this.state.panelOpen;
        LS.set("panel", this.state.panelOpen);
    }

    scrollTo(id) {
        const el = this.contentRef.el?.querySelector(`[id="${CSS.escape(id)}"]`);
        el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // ------------------------------------------------------------------
    // Lesen/Schreiben umschalten & Speichern
    // ------------------------------------------------------------------
    async toggleMode() {
        if (!this.canWrite) {
            return;
        }
        if (this.state.mode === "write") {
            await this.save();
            this.state.mode = "read";
            this.state.collabJoin = null;
            this.sync = null;
            this.render_();
        } else {
            await this.joinCollab();
            this.state.mode = "write";
        }
        LS.set("mode", this.state.mode);
        this.heartbeat(); // andere sofort informieren, ob ich gerade bearbeite
    }

    // ------------------------------------------------------------------
    // Live-Mitschreiben
    // ------------------------------------------------------------------
    /** Sitzung für die offene Seite beitreten (bzw. starten). Danach zeigt das Template den Editor. */
    async joinCollab() {
        const page = this.state.page;
        if (!page?.is_page || !this.pageWritable || this.state.collabOff) {
            return;
        }
        try {
            const join = await this.orm.call("wissen.vault", "collab_join", [page.path]);
            if (this.state.page === page) {
                this.state.collabJoin = join;
                this.state.editorKey++;
            }
        } catch (e) {
            this.state.collabOff = true;
            throw e;
        }
    }

    get editorReady() {
        return this.state.collabOff || !!this.state.collabJoin || !this.pageWritable;
    }

    get collabProps() {
        const join = this.state.collabJoin;
        if (!join || this.state.collabOff) {
            return undefined;
        }
        return { join, ...this.collabCallbacks };
    }

    /** Callbacks für den Editor. In setup() angelegt, damit "this" sicher die Komponente ist
     *  (Getter werden aus dem Template über einen abgeleiteten Kontext aufgerufen). */
    makeCollabCallbacks() {
        return {
            onReady: (sync, editor) => {
                this.sync = sync;
                this.syncEditor = editor;
            },
            onLocalChange: () => {
                this.localDirty = true;
                this.state.dirty = true;
                clearTimeout(this.autosaveTimer);
                this.autosaveTimer = setTimeout(() => this.save(), AUTOSAVE_MS);
            },
            onReset: async () => {
                // Sitzung wurde neu gestartet (z. B. nach langer Pause): ungespeichertes zuerst sichern
                await this.save();
                this.sync = null;
                this.state.collabJoin = null;
                await this.joinCollab();
            },
            onError: () => {
                this.notification.add("Live-Abgleich ist für diese Seite gerade nicht möglich. Du bearbeitest sie allein weiter; Änderungen anderer werden beim Speichern zusammengeführt.", { type: "warning" });
                this.sync = null;
                this.state.collabOff = true;
                this.state.editorKey++;
            },
        };
    }

    onEditorChange(value) {
        this.draft = value;
        if (this.sync) {
            // Bei gemeinsamer Bearbeitung zählen nur eigene Änderungen als "ungespeichert"
            this.state.dirty = this.localDirty;
            return;
        }
        this.state.dirty = value !== this.state.page.content;
        clearTimeout(this.autosaveTimer);
        if (this.state.dirty) {
            this.autosaveTimer = setTimeout(() => this.save(), AUTOSAVE_MS);
        }
    }

    async save({ force = false } = {}) {
        clearTimeout(this.autosaveTimer);
        const page = this.state.page;
        if (!page?.is_page || (!this.state.dirty && !force) || this.state.saving) {
            return;
        }
        if (this.sync) {
            return this.saveCollab(page, { force });
        }
        const content = this.draft;
        this.state.saving = true;
        try {
            const res = await this.orm.call("wissen.vault", "write_page", [page.path, content, page.mtime, force]);
            if (res.conflict) {
                // Jemand anderes hat gespeichert: erst automatisch zusammenführen
                const merged = await mergeThreeWay(page.content, content, res.content);
                if (merged !== null) {
                    const res2 = await this.orm.call("wissen.vault", "write_page", [page.path, merged, res.mtime, false]);
                    if (!res2.conflict) {
                        this.applySaved(page, res2, merged);
                        if (merged !== content) {
                            this.draft = merged;
                            this.state.editorKey++; // Editor mit zusammengeführtem Stand neu laden
                            this.render_();
                        }
                        this.notification.add(
                            `${res.last_editor || "Jemand"} hat gleichzeitig geändert – beide Änderungen wurden zusammengeführt.`,
                            { type: "info" }
                        );
                        return;
                    }
                }
                this.dialog.add(ConflictDialog, {
                    onOverwrite: () => this.save({ force: true }),
                    onReload: async () => {
                        this.state.dirty = false;
                        await this.openPage(page.path);
                    },
                });
                return;
            }
            this.applySaved(page, res, content);
        } finally {
            this.state.saving = false;
        }
    }

    async saveCollab(page, { force = false } = {}) {
        const sync = this.sync;
        if (!this.localDirty && !force) {
            return;
        }
        this.state.saving = true;
        try {
            // Nur einen vom Server bestätigten Stand speichern
            if (!(await sync.flush())) {
                this.autosaveTimer = setTimeout(() => this.save(), AUTOSAVE_MS);
                return;
            }
            const seq = sync.localSeq;
            const content = this.draft;
            const res = await this.orm.call("wissen.vault", "write_page", [
                page.path, content, null, force, { token: sync.token, version: sync.version },
            ]);
            if (res.conflict) {
                // Datei wurde außerhalb der gemeinsamen Bearbeitung geändert: zusammenführen und live verteilen
                const merged = await mergeThreeWay(res.base, content, res.content);
                if (merged !== null && merged !== content && this.syncEditor) {
                    this.syncEditor.setContent(merged);
                    this.notification.add(
                        `${res.last_editor || "Jemand"} hat die Seite zwischendurch geändert – die Änderungen wurden übernommen.`,
                        { type: "info" }
                    );
                } else if (merged === null) {
                    this.state.saving = false;
                    return this.saveCollab(page, { force: true });
                }
                return;
            }
            this.applySaved(page, res, content);
            if (sync.localSeq === seq) {
                this.localDirty = false;
                this.state.dirty = false;
            }
        } finally {
            this.state.saving = false;
        }
    }

    applySaved(page, res, content) {
        page.mtime = res.mtime;
        page.content = content;
        if (res.meta) {
            page.meta = res.meta;
        }
        this.state.dirty = this.sync ? this.localDirty : this.draft !== content;
        this.state.lastSaved = new Date();
        clearTimeout(this.sidebarTimer);
        this.sidebarTimer = setTimeout(() => this.loadSidebar(), 3000);
    }

    // ------------------------------------------------------------------
    // Zusammenarbeit: Anwesenheit, externe Änderungen
    // ------------------------------------------------------------------
    async heartbeat() {
        const page = this.state.page;
        if (!page || document.hidden) {
            return;
        }
        let res;
        try {
            res = await this.orm.silent.call("wissen.vault", "heartbeat", [page.path, this.state.mode === "write"]);
        } catch {
            return;
        }
        if (this.state.page !== page) {
            return;
        }
        const wasEditing = new Set(this.state.others.filter((o) => o.editing).map((o) => o.id));
        this.state.others = res.others;
        for (const o of res.others) {
            if (o.editing && !wasEditing.has(o.id) && this.state.mode === "write") {
                this.notification.add(`${o.name} schreibt jetzt live mit. Ihr seht die Änderungen des anderen sofort.`, { type: "info" });
            }
        }
        if (res.meta) {
            page.meta = res.meta;
        }
        // Datei wurde woanders gespeichert und ich habe nichts Ungespeichertes -> neu laden
        if (page.is_page && res.mtime && page.mtime && Math.abs(res.mtime - page.mtime) > 0.001
                && !this.state.dirty && !this.state.saving && !(this.state.mode === "write" && this.sync)) {
            const who = res.meta?.last_editor?.name || "außerhalb von Odoo";
            await this.openPage(page.path, { keepScroll: true });
            this.notification.add(`Seite wurde aktualisiert (${who}).`, { type: "info" });
        }
    }

    // ------------------------------------------------------------------
    // Seitenleiste: Favoriten & zuletzt bearbeitet
    // ------------------------------------------------------------------
    async loadSidebar() {
        try {
            this.state.sidebar = await this.orm.silent.call("wissen.vault", "sidebar_data", []);
        } catch {
            // nicht kritisch
        }
    }

    toggleSection(key) {
        this.state.sections[key] = !this.state.sections[key];
        LS.set("sections", this.state.sections);
    }

    async toggleFavorite() {
        const page = this.state.page;
        const fav = await this.orm.call("wissen.vault", "toggle_favorite", [page.path]);
        page.meta = { ...page.meta, favorite: fav };
        await this.loadSidebar();
    }

    // ------------------------------------------------------------------
    // Interaktion im gerenderten Inhalt
    // ------------------------------------------------------------------
    decorateContent() {
        const el = this.contentRef.el;
        if (!el || this.state.mode !== "read") {
            return;
        }
        if (this.htmlRef.el) {
            this.htmlRef.el.innerHTML = String(this.state.html || "");
        }
        const tasks = el.querySelectorAll("input.o_wm_task");
        tasks.forEach((cb) => (cb.disabled = !this.pageWritable));
        for (const m of el.querySelectorAll(".o_wm_mention")) {
            decorateMention(this.orm, m);
        }
        decorateReadView(el, {
            orm: this.orm,
            index: this.index,
            path: this.state.page?.path,
            canCreateTasks: this.pageWritable && this.config.has_project,
        }).then(() => highlightQuotes(el, this.state.commentQuotes));
    }

    get draftOrContent() {
        return this.draft ?? this.state.page.content;
    }

    /** Für /vorlage im Editor: Vorlagen-Liste und Inhalt laden. */
    get templatesLoader() {
        return {
            list: () => this.orm.call("wissen.vault", "list_templates", []),
            content: async (path) => {
                const tpl = await this.orm.call("wissen.vault", "read_page", [path]);
                return tpl.content
                    .replace(/\{\{\s*title\s*\}\}/g, this.state.page.name)
                    .replace(/\{\{\s*date(?::[^}]*)?\s*\}\}/g, luxon.DateTime.now().toFormat("dd.LL.yyyy"))
                    .replace(/\{\{\s*time(?::[^}]*)?\s*\}\}/g, luxon.DateTime.now().toFormat("HH:mm"));
            },
        };
    }

    get pageWritable() {
        return this.canWrite && this.state.page?.can_write !== false;
    }

    onCommentQuotes(quotes) {
        this.state.commentQuotes = quotes;
        const el = this.contentRef.el?.querySelector(".o_wm_content");
        if (el) {
            highlightQuotes(el, quotes);
        }
    }

    onContentMouseUp() {
        const sel = window.getSelection();
        const text = sel?.toString().trim();
        const scroller = this.contentRef.el;
        if (!text || text.length < 3 || !sel.rangeCount || !scroller) {
            this.state.selectionBtn = null;
            return;
        }
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        const box = scroller.getBoundingClientRect();
        this.state.selectionBtn = {
            text: text.slice(0, 300),
            top: rect.top - box.top + scroller.scrollTop - 38,
            left: Math.max(8, rect.left - box.left + rect.width / 2 - 60),
        };
    }

    commentOnSelection() {
        this.state.pendingQuote = this.state.selectionBtn?.text || null;
        this.state.selectionBtn = null;
        this.state.panelOpen = true;
        this.state.panelTab = "comments";
        window.getSelection()?.removeAllRanges();
    }

    async onContentClick(ev) {
        const toTask = ev.target.closest(".o_wm_task_to_odoo");
        if (toTask) {
            ev.preventDefault();
            return this.taskFromCheckbox(parseInt(toTask.dataset.taskIndex, 10), toTask.dataset.taskText);
        }
        const task = ev.target.closest("input.o_wm_task");
        if (task) {
            return this.onTaskToggle(ev, task);
        }
        const a = ev.target.closest("a");
        if (!a) {
            return;
        }
        if (a.classList.contains("o_wm_wikilink")) {
            ev.preventDefault();
            const path = a.dataset.wmPath;
            const anchor = a.dataset.wmAnchor || null;
            if (path && path === this.state.currentPath && anchor) {
                return this.scrollTo(anchor);
            }
            if (path) {
                return this.openPage(path, { anchor });
            }
            if (this.canWrite) {
                const target = a.dataset.wmTarget;
                const parent = target.includes("/") ? dirname(target) : dirname(this.state.currentPath || "");
                return this.createPage(parent, basename(target));
            }
            this.notification.add("Diese Seite existiert noch nicht.", { type: "warning" });
        } else if (a.classList.contains("o_wm_mention")) {
            ev.preventDefault();
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: a.dataset.wmModel,
                res_id: parseInt(a.dataset.wmId, 10),
                views: [[false, "form"]],
                target: "current",
            });
        } else if (a.classList.contains("o_wm_tag")) {
            ev.preventDefault();
            this.state.query = "#" + a.dataset.wmTag;
            this.runSearch();
        }
    }

    async onTaskToggle(ev, checkbox) {
        if (!this.pageWritable) {
            ev.preventDefault();
            return;
        }
        const all = [...this.contentRef.el.querySelectorAll(".o_wm_page > .o_wm_content input.o_wm_task")].filter(
            (cb) => !cb.closest(".o_wm_embed")
        );
        const n = all.indexOf(checkbox);
        this.draft = toggleTask(this.draft ?? this.state.page.content, n);
        this.state.dirty = true;
        await this.save();
    }

    // ------------------------------------------------------------------
    // Suche
    // ------------------------------------------------------------------
    onSearchInput(ev) {
        this.state.query = ev.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.runSearch(), 250);
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.clearSearch();
            ev.target.blur();
        } else if (ev.key === "Enter" && this.state.results?.length) {
            this.openPage(this.state.results[0].path);
        }
    }

    async runSearch() {
        const q = this.state.query.trim();
        if (!q) {
            this.state.results = null;
            return;
        }
        this.state.results = await this.orm.call("wissen.vault", "search", [q]);
    }

    clearSearch() {
        this.state.query = "";
        this.state.results = null;
    }

    highlight(snippet) {
        const q = this.state.query.trim();
        let html = escapeHtml(snippet);
        if (q) {
            const re = new RegExp(escapeHtml(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
            html = html.replace(re, (m) => `<mark>${m}</mark>`);
        }
        return markup(html);
    }

    // ------------------------------------------------------------------
    // Seiten-Operationen
    // ------------------------------------------------------------------
    async createPage(parentPath = "", title = "") {
        if (!this.canWrite) {
            return;
        }
        const templates = await this.orm.call("wissen.vault", "list_templates", []);
        this.dialog.add(CreatePageDialog, {
            tree: this.state.tree,
            templates,
            parentPath: parentPath ?? "",
            title,
            onConfirm: async ({ title, parent, template }) => {
                const path = await this.orm.call("wissen.vault", "create_page", [parent, title, template]);
                await this.loadTree();
                if (this.state.page?.is_page) {
                    this.render_(); // fehlende Links könnten jetzt aufgelöst sein
                }
                this.state.mode = "write";
                LS.set("mode", "write");
                await this.openPage(path);
            },
        });
    }

    createFolder(parentPath = "") {
        this.dialog.add(PromptDialog, {
            title: "Neuer Ordner",
            label: "Name",
            confirmLabel: "Anlegen",
            onConfirm: async (name) => {
                const path = await this.orm.call("wissen.vault", "create_folder", [parentPath, name]);
                await this.loadTree();
                this.expandTo(path + "/x");
            },
        });
    }

    renamePage() {
        const page = this.state.page;
        this.dialog.add(PromptDialog, {
            title: "Umbenennen",
            label: "Neuer Name",
            value: page.name,
            confirmLabel: "Umbenennen",
            onConfirm: async (name) => {
                await this.save();
                const res = await this.orm.call("wissen.vault", "rename", [page.path, name]);
                await this.afterStructureChange(res.path, res.updated_files);
            },
        });
    }

    moveDialog() {
        const page = this.state.page;
        this.dialog.add(MoveDialog, {
            tree: this.state.tree,
            path: page.path,
            name: page.name,
            onConfirm: (target) => this.movePage(page.path, target),
        });
    }

    async movePage(path, target) {
        if (path === this.state.currentPath) {
            await this.save();
        }
        const res = await this.orm.call("wissen.vault", "move", [path, target]);
        if (target) {
            this.state.expanded[target] = true;
        }
        const openPath = path === this.state.currentPath ? res.path : this.state.currentPath;
        await this.afterStructureChange(openPath, res.updated_files);
    }

    async afterStructureChange(openPath, updatedFiles) {
        await this.loadTree();
        await this.openPage(openPath);
        if (updatedFiles) {
            this.notification.add(`Links in ${updatedFiles} Seite(n) wurden angepasst.`, { type: "info" });
        }
    }

    async duplicatePage() {
        await this.save();
        const path = await this.orm.call("wissen.vault", "duplicate", [this.state.page.path]);
        await this.loadTree();
        await this.openPage(path);
        this.notification.add("Seite dupliziert", { type: "success" });
    }

    deletePage() {
        const page = this.state.page;
        const sub = page.children.length ? " inklusive aller Unterseiten" : "";
        this.dialog.add(ConfirmationDialog, {
            title: "Löschen",
            body: `„${page.name}“${sub} in den Papierkorb verschieben? Dort lässt sie sich ${TRASH_DAYS} Tage lang wiederherstellen.`,
            confirmLabel: "Löschen",
            confirmClass: "btn-danger",
            confirm: async () => {
                this.state.dirty = false;
                clearTimeout(this.autosaveTimer);
                await this.orm.call("wissen.vault", "delete", [page.path]);
                LS.set("last", null);
                await this.loadTree();
                const parent = page.breadcrumbs.at(-1)?.path;
                this.state.page = null;
                this.state.currentPath = null;
                await this.openPage(parent || this.firstPage(), { silent: true });
            },
            cancel: () => {},
        });
    }

    async copyLink() {
        // Odoo-Link zur Seite: funktioniert in Chatter, E-Mails, Aufgaben und im Editor
        const url = `${window.location.origin}/odoo/action-wissensmanagement.action_wm_app?wm_path=${encodeURIComponent(this.state.page.path)}`;
        await navigator.clipboard.writeText(url);
        this.notification.add("Link zur Seite kopiert", { type: "success" });
    }

    openTrash() {
        this.dialog.add(TrashDialog, {
            isManager: !!this.config.is_manager,
            onRestored: async (path) => {
                await this.loadTree();
                await this.openPage(path);
            },
        });
    }

    taskFromCheckbox(taskIndex, text) {
        this.dialog.add(TaskDialog, {
            path: this.state.page.path,
            taskIndex,
            name: text || "",
            onCreated: async (res) => {
                this.notification.add("Odoo-Aufgabe angelegt und auf der Seite verlinkt.", { type: "success" });
                await this.openPage(this.state.page.path, { keepScroll: true });
            },
        });
    }

    showHistory() {
        const page = this.state.page;
        this.dialog.add(HistoryDialog, {
            path: page.path,
            current: this.draft ?? page.content,
            index: this.index,
            canWrite: this.pageWritable,
            onRestored: async () => {
                this.state.dirty = false;
                await this.openPage(page.path);
                this.notification.add("Version wiederhergestellt.", { type: "success" });
            },
        });
    }

    async showShare() {
        await this.save();
        const page = this.state.page;
        this.dialog.add(ShareDialog, {
            path: page.path,
            name: page.name,
            canWrite: this.pageWritable,
            onChanged: (mode) => {
                page.meta = { ...page.meta, share_mode: mode };
            },
        });
    }

    shareToChatter() {
        this.dialog.add(ChatterDialog, { path: this.state.page.path, name: this.state.page.name });
    }

    async printPage() {
        await this.save();
        window.open(`/wissensmanagement/print?path=${encodeURIComponent(this.state.page.path)}`, "_blank");
    }

    openOverview() {
        this.state.overview = true;
    }

    filterTag(tag) {
        this.state.overview = false;
        this.state.query = "#" + tag;
        this.runSearch();
    }

    openRecord(model, id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]] });
    }

    openUser(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "res.users", res_id: id, views: [[false, "form"]] });
    }

    async dropOn(src, target, pos) {
        if (path_is_inside(target, src)) {
            return;
        }
        if (src === this.state.currentPath) {
            await this.save();
        }
        const res = await this.orm.call("wissen.vault", "reorder", [src, target, pos]);
        if (pos === "inside") {
            this.state.expanded[target] = true;
        }
        const openPath = src === this.state.currentPath ? res.path : this.state.currentPath;
        await this.afterStructureChange(openPath, res.updated_files);
    }

    async refresh() {
        invalidateCards();
        await this.loadTree();
        if (this.state.currentPath !== null) {
            await this.openPage(this.state.currentPath, { silent: true });
        }
    }

    openSettings() {
        this.action.doAction("wissensmanagement.action_wm_settings");
    }

    // Drop auf den leeren Bereich des Baums = in die oberste Ebene verschieben
    onRootDragOver(ev) {
        if (this.state.dragging) {
            ev.preventDefault();
            this.state.dropTarget = "";
        }
    }

    onRootDrop(ev) {
        ev.preventDefault();
        const src = ev.dataTransfer.getData("text/x-wm-path");
        this.state.dropTarget = null;
        this.state.dragging = null;
        if (src && src.includes("/")) {
            this.movePage(src, "");
        }
    }
}

function path_is_inside(target, src) {
    const own = src.replace(/\.md$/i, "");
    const t = target.replace(/\.md$/i, "");
    return t === own || t.startsWith(own + "/");
}

registry.category("actions").add("wissensmanagement.app", KnowledgeApp);
