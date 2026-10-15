/* global luxon */
import { Component, markup, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { loadJS } from "@web/core/assets";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { escapeHtml, renderMarkdown } from "./markdown";

// ----------------------------------------------------------------------
// Hilfsfunktionen
// ----------------------------------------------------------------------

/** "vor 5 Minuten" aus einem UTC-Zeitstempel "YYYY-MM-DD HH:MM:SS". */
export function relTime(utc) {
    if (!utc) {
        return "";
    }
    const dt = luxon.DateTime.fromSQL(utc, { zone: "utc" }).setLocale("de");
    return Math.abs(dt.diffNow("minutes").minutes) < 1 ? "gerade eben" : dt.toRelative();
}

export function absTime(utc) {
    if (!utc) {
        return "";
    }
    return luxon.DateTime.fromSQL(utc, { zone: "utc" }).setLocale("de").toLocal().toFormat("dd.LL.yyyy, HH:mm");
}

async function dmp() {
    // vollständige Bibliothek (Odoos eigene Kopie enthält keine patch-Funktionen)
    if (!window.WmDiffMatchPatch) {
        await loadJS("/wissensmanagement/static/lib/diff_match_patch/diff_match_patch.js");
    }
    return new window.WmDiffMatchPatch();
}

/**
 * Drei-Wege-Zusammenführung: meine Änderungen (base -> mine) auf den neuen Stand der
 * anderen Person (theirs) anwenden. Gibt null zurück, wenn das nicht sauber geht.
 */
export async function mergeThreeWay(base, mine, theirs) {
    const d = await dmp();
    d.Match_Threshold = 0.3;
    const patches = d.patch_make(base, mine);
    const [merged, results] = d.patch_apply(patches, theirs);
    return results.every(Boolean) ? merged : null;
}

async function diffHtml(oldText, newText) {
    const d = await dmp();
    const diffs = d.diff_main(oldText || "", newText || "");
    d.diff_cleanupSemantic(diffs);
    return diffs
        .map(([op, text]) => {
            const t = escapeHtml(text);
            return op === 1 ? `<ins>${t}</ins>` : op === -1 ? `<del>${t}</del>` : `<span>${t}</span>`;
        })
        .join("");
}

// ----------------------------------------------------------------------
// Versionsverlauf
// ----------------------------------------------------------------------
export class HistoryDialog extends Component {
    static template = "wissensmanagement.HistoryDialog";
    static components = { Dialog };
    static props = { close: Function, path: String, current: String, index: Object, canWrite: Boolean, onRestored: Function };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ versions: [], selected: null, view: "diff", html: "", loading: true });
        this.relTime = relTime;
        this.absTime = absTime;
        onWillStart(async () => {
            this.state.versions = await this.orm.call("wissen.vault", "list_versions", [this.props.path]);
            this.state.loading = false;
            if (this.state.versions.length) {
                await this.select(this.state.versions[0]);
            }
        });
    }

    async select(version) {
        this.state.selected = version;
        const { content } = await this.orm.call("wissen.vault", "get_version", [version.id]);
        this.content = content;
        await this.renderSelected();
    }

    async setView(view) {
        this.state.view = view;
        await this.renderSelected();
    }

    async renderSelected() {
        if (this.state.view === "diff") {
            // Unterschied: diese Version -> aktueller Stand
            this.state.html = markup(await diffHtml(this.content, this.props.current));
        } else {
            this.state.html = markup(renderMarkdown(this.content, { index: this.props.index, path: this.props.path }).html);
        }
    }

    async restore() {
        await this.orm.call("wissen.vault", "restore_version", [this.state.selected.id]);
        await this.props.onRestored();
        this.props.close();
    }
}

// ----------------------------------------------------------------------
// Freigabe: öffentlicher Link / Kundenportal
// ----------------------------------------------------------------------
export class ShareDialog extends Component {
    static template = "wissensmanagement.ShareDialog";
    static components = { Dialog };
    static props = { close: Function, path: String, name: String, canWrite: Boolean, onChanged: Function };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ info: null, term: "", suggestions: [] });
        onWillStart(async () => {
            this.state.info = await this.orm.call("wissen.vault", "get_share", [this.props.path]);
        });
    }

    async setMode(mode) {
        const ids = this.state.info.partners.map((p) => p.id);
        this.state.info = await this.orm.call("wissen.vault", "set_share", [this.props.path, mode, ids]);
        this.props.onChanged(mode);
    }

    async searchPartner(ev) {
        this.state.term = ev.target.value;
        if (this.state.term.length < 2) {
            this.state.suggestions = [];
            return;
        }
        const res = await this.orm.call("res.partner", "name_search", [this.state.term], { limit: 8 });
        this.state.suggestions = res.map(([id, name]) => ({ id, name }));
    }

    async addPartner(p) {
        const ids = [...new Set([...this.state.info.partners.map((x) => x.id), p.id])];
        this.state.info = await this.orm.call("wissen.vault", "set_share", [this.props.path, "portal", ids]);
        this.state.term = "";
        this.state.suggestions = [];
    }

    async removePartner(p) {
        const ids = this.state.info.partners.map((x) => x.id).filter((id) => id !== p.id);
        this.state.info = await this.orm.call("wissen.vault", "set_share", [this.props.path, "portal", ids]);
    }

    async regenerate() {
        this.state.info = await this.orm.call("wissen.vault", "regenerate_share_token", [this.props.path]);
        this.notification.add("Neuer Link erzeugt – der alte funktioniert nicht mehr.", { type: "info" });
    }

    async copy(url) {
        await navigator.clipboard.writeText(url);
        this.notification.add("Link kopiert", { type: "success" });
    }
}

// ----------------------------------------------------------------------
// Checkbox -> Odoo-Aufgabe
// ----------------------------------------------------------------------
export class TaskDialog extends Component {
    static template = "wissensmanagement.TaskDialog";
    static components = { Dialog };
    static props = { close: Function, path: String, taskIndex: Number, name: String, onCreated: Function };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ name: this.props.name, projects: [], project_id: "", me: true, deadline: "" });
        onWillStart(async () => {
            this.state.projects = await this.orm.searchRead("project.project", [], ["display_name"], { limit: 200 });
            const last = localStorage.getItem("wm_last_project");
            if (last && this.state.projects.some((p) => String(p.id) === last)) {
                this.state.project_id = last;
            }
        });
    }

    async confirm() {
        const vals = { name: this.state.name, project_id: parseInt(this.state.project_id, 10) || false };
        if (this.state.me) {
            vals.user_ids = [user.userId];
        }
        if (this.state.deadline) {
            vals.date_deadline = this.state.deadline;
        }
        try {
            localStorage.setItem("wm_last_project", this.state.project_id);
        } catch {
            // egal
        }
        const res = await this.orm.call("wissen.vault", "create_task_from_line", [this.props.path, this.props.taskIndex, vals]);
        await this.props.onCreated(res);
        this.props.close();
    }
}

// ----------------------------------------------------------------------
// Seite im Chatter eines Datensatzes teilen
// ----------------------------------------------------------------------
export class ChatterDialog extends Component {
    static template = "wissensmanagement.ChatterDialog";
    static components = { Dialog };
    static props = { close: Function, path: String, name: String };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ term: "", results: [], selected: null, note: "" });
    }

    async search(ev) {
        this.state.term = ev.target.value;
        if (this.state.term.length < 1) {
            this.state.results = [];
            return;
        }
        this.state.results = await this.orm.call("wissen.vault", "search_mentions", [this.state.term]);
    }

    async confirm() {
        const r = this.state.selected;
        if (!r) {
            return;
        }
        await this.orm.call("wissen.vault", "share_to_chatter", [this.props.path, r.model, r.id, this.state.note]);
        this.notification.add(`In den Chatter von „${r.name}“ gepostet`, { type: "success" });
        this.props.close();
    }
}

// ----------------------------------------------------------------------
// Papierkorb
// ----------------------------------------------------------------------
export const TRASH_DAYS = 30;

export class TrashDialog extends Component {
    static template = "wissensmanagement.TrashDialog";
    static components = { Dialog };
    static props = { close: Function, isManager: Boolean, onRestored: Function };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ items: [], loading: true, confirmEmpty: false });
        this.relTime = relTime;
        this.trashDays = TRASH_DAYS;
        onWillStart(() => this.load());
    }

    async load() {
        this.state.items = await this.orm.call("wissen.vault", "list_trash", []);
        this.state.loading = false;
    }

    daysLeft(item) {
        const age = (Date.now() - luxon.DateTime.fromSQL(item.date, { zone: "utc" }).toMillis()) / 86400000;
        return Math.max(0, Math.ceil(TRASH_DAYS - age));
    }

    async restore(item) {
        const path = await this.orm.call("wissen.vault", "restore_trash", [item.id]);
        this.notification.add(`„${item.name}“ wiederhergestellt`, { type: "success" });
        this.props.close();
        await this.props.onRestored(path);
    }

    async purge(item) {
        await this.orm.call("wissen.vault", "purge_trash", [item.id]);
        await this.load();
    }

    async emptyAll() {
        await this.orm.call("wissen.vault", "empty_trash", []);
        this.state.confirmEmpty = false;
        await this.load();
    }
}
