import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Flache Liste aller möglichen Elternelemente (Root, Ordner, Seiten) aus dem Baum. */
export function flattenTree(nodes, depth = 0, out = []) {
    for (const n of nodes) {
        out.push({ path: n.path, name: n.name, depth, is_page: n.is_page, folder_path: n.folder_path });
        flattenTree(n.children, depth + 1, out);
    }
    return out;
}

export class PromptDialog extends Component {
    static template = "wissensmanagement.PromptDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        title: String,
        label: { type: String, optional: true },
        value: { type: String, optional: true },
        confirmLabel: { type: String, optional: true },
        onConfirm: Function,
    };

    setup() {
        this.state = useState({ value: this.props.value || "" });
        this.inputRef = useRef("input");
        onMounted(() => {
            this.inputRef.el.focus();
            this.inputRef.el.select();
        });
    }

    async confirm() {
        if (!this.state.value.trim()) {
            return;
        }
        await this.props.onConfirm(this.state.value.trim());
        this.props.close();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }
}

export class CreatePageDialog extends Component {
    static template = "wissensmanagement.CreatePageDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        tree: Array,
        templates: Array,
        parentPath: { type: String, optional: true },
        title: { type: String, optional: true },
        onConfirm: Function,
    };

    setup() {
        this.parents = flattenTree(this.props.tree);
        this.state = useState({
            title: this.props.title || "",
            parent: this.props.parentPath || "",
            template: "",
            filter: "",
        });
        this.inputRef = useRef("input");
        onMounted(() => this.inputRef.el.focus());
    }

    get filteredTemplates() {
        const f = this.state.filter.toLowerCase();
        return this.props.templates.filter((t) => !f || t.name.toLowerCase().includes(f));
    }

    selectTemplate(path) {
        this.state.template = path;
        this.inputRef.el?.focus();
    }

    async confirm() {
        if (!this.state.title.trim()) {
            this.inputRef.el.focus();
            return;
        }
        await this.props.onConfirm({
            title: this.state.title.trim(),
            parent: this.state.parent,
            template: this.state.template || null,
        });
        this.props.close();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }
}

export class MoveDialog extends Component {
    static template = "wissensmanagement.MoveDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        tree: Array,
        path: String,
        name: String,
        onConfirm: Function,
    };

    setup() {
        const own = this.props.path.replace(/\.md$/i, "");
        this.targets = flattenTree(this.props.tree).filter((t) => {
            const p = (t.folder_path || t.path).replace(/\.md$/i, "");
            return t.path !== this.props.path && p !== own && !p.startsWith(own + "/");
        });
        this.state = useState({ filter: "", target: null });
    }

    get filtered() {
        const f = this.state.filter.toLowerCase();
        return this.targets.filter((t) => !f || t.path.toLowerCase().includes(f));
    }

    async confirm() {
        if (this.state.target === null) {
            return;
        }
        await this.props.onConfirm(this.state.target);
        this.props.close();
    }
}

export class ConflictDialog extends Component {
    static template = "wissensmanagement.ConflictDialog";
    static components = { Dialog };
    static props = { close: Function, onOverwrite: Function, onReload: Function };

    async overwrite() {
        await this.props.onOverwrite();
        this.props.close();
    }

    async reload() {
        await this.props.onReload();
        this.props.close();
    }
}
