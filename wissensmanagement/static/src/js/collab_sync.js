/* global toastui */
// Live-Mitschreiben im Editor (siehe models/wissen_collab.py für den Server-Teil).
//
// Alle Mitschreibenden starten vom selben Ausgangstext und wenden dieselben ProseMirror-Schritte
// in derselben Reihenfolge an. prosemirror-collab übernimmt das Umsortieren (Rebase) eigener,
// noch nicht bestätigter Schritte, wenn jemand anderes schneller war.

const PUSH_DELAY = 120; // ms: Tastenanschläge kurz bündeln
const POLL_MS = 4000; // Sicherheitsnetz, falls eine Bus-Nachricht verloren geht
const CURSOR_DELAY = 250;
const CURSOR_TTL = 60000;
const REMOTE = "wmCollabRemote";
const CURSORS = "wmCollabCursors";

const COLORS = ["#e11d48", "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

export function userColor(userId) {
    return COLORS[Math.abs(Number(userId) || 0) % COLORS.length];
}

export class CollabSync {
    /**
     * @param {Object} opts
     * @param {Object} opts.editor  Toast-UI-Editor (WYSIWYG)
     * @param {Object} opts.join    Antwort von collab_join: { token, version, steps }
     * @param {string} opts.path
     * @param {Object} opts.orm
     * @param {Object} opts.bus     bus_service
     * @param {number} opts.userId
     * @param {Function} opts.onLocalChange  eigene Änderung (nicht von anderen) -> speichern
     * @param {Function} opts.onReset        Sitzung neu gestartet -> Editor neu laden
     * @param {Function} opts.onError        Abgleich nicht möglich
     * @param {Function} opts.onPeers        Liste der aktiven Mitschreibenden geändert
     * @param {Function} opts.onRemote       fremde Änderung eingespielt (ProseMirror-Mapping), z. B. um
     *                                       gemerkte Positionen offener Menüs nachzuziehen
     */
    constructor(opts) {
        Object.assign(this, opts);
        this.pm = toastui.pm;
        this.view = this.editor.wwEditor.view;
        this.token = opts.join.token;
        this.clientId = `${this.userId}-${Math.random().toString(36).slice(2, 10)}`;
        this.cursors = new Map(); // clientId -> { userId, name, anchor, head, ts }
        this.pushing = false;
        this.pulling = false;
        this.pullAgain = false;
        this.destroyed = false;
        this.localSeq = 0;
    }

    /** Bisherige Schritte anwenden und Collab-Plugin aktivieren. Gibt false zurück, wenn das nicht passt. */
    start() {
        const { Step, collab, Plugin, PluginKey, DecorationSet } = this.pm;
        const view = this.view;
        const schema = view.state.schema;
        const steps = this.join.steps || [];
        if (steps.length) {
            const tr = view.state.tr;
            for (const s of steps) {
                if (tr.maybeStep(Step.fromJSON(schema, s.step)).failed) {
                    return false;
                }
            }
            tr.setMeta("addToHistory", false).setMeta(REMOTE, true);
            view.dispatch(tr);
        }
        const self = this;
        const cursorKey = new PluginKey("wmCollabCursors");
        this.cursorKey = cursorKey;
        const watcher = new Plugin({
            key: new PluginKey("wmCollabWatch"),
            state: {
                init: () => DecorationSet.empty,
                apply(tr, deco, _old, state) {
                    if (tr.docChanged && !tr.getMeta(REMOTE) && !tr.getMeta("collab$")) {
                        self.localSeq++;
                        self.schedulePush();
                        queueMicrotask(() => self.onLocalChange?.());
                    }
                    const fresh = tr.getMeta(CURSORS);
                    if (fresh) {
                        return self.buildCursorDecorations(state.doc);
                    }
                    return deco.map(tr.mapping, tr.doc);
                },
            },
            props: {
                decorations(state) {
                    return this.getState(state);
                },
            },
            view: () => ({
                update: (v, prev) => {
                    if (!v.state.selection.eq(prev.selection) || !v.state.doc.eq(prev.doc)) {
                        self.scheduleCursor();
                    }
                },
            }),
        });
        const newState = view.state.reconfigure({
            plugins: view.state.plugins.concat([
                collab({ version: this.join.version + 0, clientID: this.clientId }),
                watcher,
            ]),
        });
        view.updateState(newState);

        this.onBus = (payload) => this.handleBus(payload);
        this.bus.subscribe("wissen_collab", this.onBus);
        this.bus.start();
        this.pollTimer = setInterval(() => this.pull(), POLL_MS);
        this.cursorCleanup = setInterval(() => this.dropStaleCursors(), 10000);
        this.scheduleCursor();
        return true;
    }

    destroy() {
        if (this.destroyed) {
            return;
        }
        this.destroyed = true;
        clearInterval(this.pollTimer);
        clearInterval(this.cursorCleanup);
        clearTimeout(this.pushTimer);
        clearTimeout(this.cursorTimer);
        this.bus.unsubscribe("wissen_collab", this.onBus);
        this.orm.silent.call("wissen.vault", "collab_leave", [this.path, this.token, this.clientId]).catch(() => {});
    }

    get version() {
        return this.pm.getVersion(this.view.state);
    }

    /** true, wenn alle eigenen Änderungen vom Server bestätigt sind. */
    isSynced() {
        return !this.pm.sendableSteps(this.view.state);
    }

    /** Wartet (höchstens ``timeout`` ms), bis alle eigenen Schritte bestätigt sind. */
    async flush(timeout = 4000) {
        const until = Date.now() + timeout;
        while (!this.destroyed && !this.isSynced() && Date.now() < until) {
            await this.push();
            if (!this.isSynced()) {
                await new Promise((r) => setTimeout(r, 150));
            }
        }
        return this.isSynced();
    }

    // ------------------------------------------------------------------
    // Senden und Empfangen
    // ------------------------------------------------------------------
    schedulePush() {
        clearTimeout(this.pushTimer);
        this.pushTimer = setTimeout(() => this.push(), PUSH_DELAY);
    }

    async push() {
        if (this.destroyed || this.pushing) {
            return;
        }
        const sendable = this.pm.sendableSteps(this.view.state);
        if (!sendable) {
            return;
        }
        this.pushing = true;
        let res;
        try {
            res = await this.orm.silent.call("wissen.vault", "collab_push", [
                this.path, this.token, sendable.version, sendable.steps.map((s) => s.toJSON()), String(sendable.clientID),
            ]);
        } catch {
            res = { ok: false };
        } finally {
            this.pushing = false;
        }
        if (this.destroyed) {
            return;
        }
        if (res.reset) {
            return this.onReset();
        }
        // Angenommen: eigene Schritte per Pull bestätigen. Abgelehnt: fremde Schritte holen, dann erneut senden.
        await this.pull();
        if (this.pm.sendableSteps(this.view.state)) {
            this.schedulePush();
        }
    }

    async pull() {
        if (this.destroyed) {
            return;
        }
        if (this.pulling) {
            this.pullAgain = true;
            return;
        }
        this.pulling = true;
        try {
            do {
                this.pullAgain = false;
                let res;
                try {
                    res = await this.orm.silent.call("wissen.vault", "collab_pull", [this.path, this.token, this.version]);
                } catch {
                    return;
                }
                if (this.destroyed) {
                    return;
                }
                if (res.reset) {
                    return this.onReset();
                }
                if (res.steps.length) {
                    this.applyRemote(res.steps);
                }
            } while (this.pullAgain);
        } finally {
            this.pulling = false;
        }
    }

    /** Noch nicht verarbeitete Tastatureingaben (DOM-Änderungen) zuerst in eigene Schritte umwandeln,
     *  sonst würden sie nach dem Einspielen fremder Schritte gegen den falschen Stand gelesen. */
    flushInput() {
        try {
            this.view.domObserver?.flush();
        } catch {
            // interne API, im Zweifel ohne
        }
    }

    applyRemote(steps) {
        const { Step, receiveTransaction } = this.pm;
        const view = this.view;
        this.flushInput();
        const start = this.version;
        // nur lückenlos anschließende Schritte übernehmen
        const fresh = steps.filter((s) => s.version >= start).sort((a, b) => a.version - b.version);
        if (!fresh.length || fresh[0].version !== start) {
            return;
        }
        let parsed;
        try {
            parsed = fresh.map((s) => Step.fromJSON(view.state.schema, s.step));
        } catch (e) {
            return this.onError(e);
        }
        const tr = receiveTransaction(view.state, parsed, fresh.map((s) => s.client_id), { mapSelection: true });
        tr.setMeta(REMOTE, true);
        view.dispatch(tr);
        this.onRemote?.(tr.mapping);
    }

    handleBus(payload) {
        if (this.destroyed || payload.path !== this.path || payload.token !== this.token) {
            return;
        }
        if (payload.kind === "steps") {
            if (payload.version > this.version) {
                this.pull();
            }
        } else if (payload.kind === "cursor") {
            if (payload.client_id === this.clientId) {
                return;
            }
            this.cursors.set(payload.client_id, { ...payload, ts: Date.now() });
            this.refreshCursors();
        } else if (payload.kind === "leave") {
            this.cursors.delete(payload.client_id);
            this.refreshCursors();
        }
    }

    // ------------------------------------------------------------------
    // Cursor der anderen
    // ------------------------------------------------------------------
    scheduleCursor() {
        clearTimeout(this.cursorTimer);
        this.cursorTimer = setTimeout(() => {
            if (this.destroyed) {
                return;
            }
            const { anchor, head } = this.view.state.selection;
            this.orm.silent.call("wissen.vault", "collab_cursor", [this.path, this.token, this.clientId, this.version, anchor, head])
                .catch(() => {});
        }, CURSOR_DELAY);
    }

    dropStaleCursors() {
        const now = Date.now();
        let changed = false;
        for (const [id, c] of this.cursors) {
            if (now - c.ts > CURSOR_TTL) {
                this.cursors.delete(id);
                changed = true;
            }
        }
        if (changed) {
            this.refreshCursors();
        }
    }

    refreshCursors() {
        if (!this.destroyed) {
            this.flushInput();
            this.view.dispatch(this.view.state.tr.setMeta(CURSORS, true).setMeta("addToHistory", false));
        }
        this.onPeers?.([...new Map([...this.cursors.values()].map((c) => [c.user_id, c])).values()]);
    }

    buildCursorDecorations(doc) {
        const { Decoration, DecorationSet } = this.pm;
        const size = doc.content.size;
        const clamp = (p) => Math.max(0, Math.min(size, p));
        const decos = [];
        for (const c of this.cursors.values()) {
            const color = userColor(c.user_id);
            const head = clamp(c.head);
            const anchor = clamp(c.anchor);
            if (anchor !== head) {
                decos.push(Decoration.inline(Math.min(anchor, head), Math.max(anchor, head), {
                    class: "o_wm_rsel",
                    style: `background-color: ${color}33`,
                }));
            }
            const caret = document.createElement("span");
            caret.className = "o_wm_rcursor";
            caret.style.borderColor = color;
            const label = document.createElement("span");
            label.className = "o_wm_rcursor_label";
            label.style.backgroundColor = color;
            label.textContent = c.name;
            caret.appendChild(label);
            decos.push(Decoration.widget(head, caret, { key: `c-${c.client_id}-${c.name}`, side: -1 }));
        }
        return DecorationSet.create(doc, decos);
    }
}
