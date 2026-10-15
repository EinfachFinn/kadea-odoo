import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { relTime } from "./collab";

export class CommentsPanel extends Component {
    static template = "wissensmanagement.CommentsPanel";
    static props = {
        path: String,
        pendingQuote: { type: [String, { value: null }], optional: true },
        onQuoteUsed: Function,
        onCountChange: Function,
        onHighlight: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ comments: [], body: "", replyTo: null, replyBody: "", showResolved: false });
        this.relTime = relTime;
        onWillStart(() => this.load(this.props.path));
        onWillUpdateProps(async (next) => {
            if (next.path !== this.props.path) {
                await this.load(next.path);
            }
        });
    }

    async load(path) {
        this.state.comments = await this.orm.call("wissen.vault", "list_comments", [path]);
        this.notify();
    }

    notify() {
        const open = this.threads.filter((t) => !t.resolved);
        this.props.onCountChange(open.length);
        this.props.onHighlight(open.map((t) => t.quote).filter(Boolean));
    }

    get threads() {
        const roots = this.state.comments.filter((c) => !c.parent_id);
        return roots.map((r) => ({ ...r, replies: this.state.comments.filter((c) => c.parent_id === r.id) }));
    }

    get visibleThreads() {
        return this.threads.filter((t) => this.state.showResolved || !t.resolved);
    }

    get resolvedCount() {
        return this.threads.filter((t) => t.resolved).length;
    }

    async add() {
        if (!this.state.body.trim()) {
            return;
        }
        await this.orm.call("wissen.vault", "add_comment", [this.props.path, this.state.body, this.props.pendingQuote || false]);
        this.state.body = "";
        this.props.onQuoteUsed();
        await this.load(this.props.path);
    }

    async reply(thread) {
        if (!this.state.replyBody.trim()) {
            return;
        }
        await this.orm.call("wissen.vault", "add_comment", [this.props.path, this.state.replyBody, false, thread.id]);
        this.state.replyBody = "";
        this.state.replyTo = null;
        await this.load(this.props.path);
    }

    async toggleResolved(thread) {
        await this.orm.call("wissen.vault", "resolve_comment", [thread.id, !thread.resolved]);
        await this.load(this.props.path);
    }

    async remove(comment) {
        await this.orm.call("wissen.vault", "delete_comment", [comment.id]);
        await this.load(this.props.path);
    }

    onKeydown(ev, fn) {
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            fn();
        }
    }
}
