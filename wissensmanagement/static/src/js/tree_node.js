import { Component, useState } from "@odoo/owl";

export class TreeNode extends Component {
    static template = "wissensmanagement.TreeNode";
    static components = {};
    static props = {
        node: Object,
        depth: Number,
        app: Object,
    };

    setup() {
        // eigenes Abo auf den App-Zustand, damit Auswahl/Aufklappen neu gezeichnet wird
        this.appState = useState(this.props.app.state);
    }

    get app() {
        return this.props.app;
    }

    get node() {
        return this.props.node;
    }

    get isOpen() {
        return this.appState.expanded[this.node.path];
    }

    get isActive() {
        return this.appState.currentPath === this.node.path;
    }

    get isTemplateFolder() {
        const tf = this.app.config?.template_folder;
        return tf && (this.node.folder_path === tf || this.node.path === tf || this.node.path === tf + ".md");
    }

    get icon() {
        if (this.isTemplateFolder) {
            return "fa-clone";
        }
        if (!this.node.is_page) {
            return this.isOpen ? "fa-folder-open-o" : "fa-folder-o";
        }
        return this.node.children.length ? "fa-files-o" : "fa-file-text-o";
    }

    toggle(ev) {
        ev.stopPropagation();
        this.app.toggleExpanded(this.node.path);
    }

    open() {
        this.app.openPage(this.node.path);
        if (this.node.children.length && !this.isOpen) {
            this.app.toggleExpanded(this.node.path);
        }
    }

    addChild(ev) {
        ev.stopPropagation();
        this.app.createPage(this.node.path);
    }

    // Drag & Drop: Seite auf andere Seite/Ordner ziehen = als Unterseite einsortieren
    onDragStart(ev) {
        if (!this.app.canWrite) {
            ev.preventDefault();
            return;
        }
        ev.stopPropagation();
        ev.dataTransfer.setData("text/x-wm-path", this.node.path);
        ev.dataTransfer.effectAllowed = "move";
        this.app.state.dragging = this.node.path;
    }

    onDragEnd() {
        this.app.state.dragging = null;
        this.app.state.dropTarget = null;
    }

    onDragOver(ev) {
        const src = this.app.state.dragging;
        if (!src || src === this.node.path) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        // oberes Viertel = davor, unteres Viertel = dahinter, Mitte = als Unterseite
        const rect = ev.currentTarget.getBoundingClientRect();
        const y = (ev.clientY - rect.top) / rect.height;
        this.appState.dropTarget = this.node.path;
        this.appState.dropPos = y < 0.28 ? "before" : y > 0.72 ? "after" : "inside";
    }

    onDragLeave() {
        if (this.appState.dropTarget === this.node.path) {
            this.appState.dropTarget = null;
        }
    }

    onDrop(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const src = ev.dataTransfer.getData("text/x-wm-path");
        const pos = this.appState.dropPos || "inside";
        this.appState.dropTarget = null;
        this.appState.dropPos = null;
        this.appState.dragging = null;
        if (src && src !== this.node.path) {
            this.app.dropOn(src, this.node.path, pos);
        }
    }
}
TreeNode.components = { TreeNode };
