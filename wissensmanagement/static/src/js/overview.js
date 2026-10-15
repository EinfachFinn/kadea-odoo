import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const PALETTE = ["#7c3aed", "#0e7490", "#d97706", "#16a34a", "#dc2626", "#2563eb", "#db2777", "#4b5563", "#0891b2", "#65a30d"];

/** Kleine Kräfte-Simulation für die Graph-Ansicht (ohne externe Bibliothek). */
class ForceGraph {
    constructor(canvas, data, onOpen, currentPath) {
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d");
        this.onOpen = onOpen;
        this.current = currentPath;
        const folders = [...new Set(data.nodes.map((n) => n.folder))];
        this.nodes = data.nodes.map((n, i) => ({
            ...n,
            x: Math.cos(i) * 200 + Math.random() * 40,
            y: Math.sin(i) * 200 + Math.random() * 40,
            vx: 0,
            vy: 0,
            r: 4 + Math.min(10, Math.sqrt(n.degree) * 2.2),
            color: PALETTE[folders.indexOf(n.folder) % PALETTE.length],
        }));
        const byId = Object.fromEntries(this.nodes.map((n) => [n.id, n]));
        this.edges = data.edges.map(([a, b]) => [byId[a], byId[b]]).filter(([a, b]) => a && b);
        this.scale = 1;
        this.offset = { x: 0, y: 0 };
        this.hover = null;
        this.drag = null;
        this.alpha = 1;
        this.resize();
        this.bind();
        this.loop = this.loop.bind(this);
        this.raf = requestAnimationFrame(this.loop);
    }

    resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        this.w = rect.width;
        this.h = rect.height;
        this.canvas.width = this.w * dpr;
        this.canvas.height = this.h * dpr;
        this.canvas.style.width = this.w + "px";
        this.canvas.style.height = this.h + "px";
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    toWorld(ev) {
        const rect = this.canvas.getBoundingClientRect();
        return {
            x: (ev.clientX - rect.left - this.w / 2 - this.offset.x) / this.scale,
            y: (ev.clientY - rect.top - this.h / 2 - this.offset.y) / this.scale,
        };
    }

    nodeAt(p) {
        return this.nodes.find((n) => (n.x - p.x) ** 2 + (n.y - p.y) ** 2 < (n.r + 4) ** 2);
    }

    bind() {
        const c = this.canvas;
        this.handlers = {
            mousemove: (ev) => {
                const p = this.toWorld(ev);
                if (this.drag?.node) {
                    Object.assign(this.drag.node, { x: p.x, y: p.y, vx: 0, vy: 0 });
                    this.drag.moved = true;
                    this.alpha = Math.max(this.alpha, 0.3);
                } else if (this.drag) {
                    this.offset.x += ev.movementX;
                    this.offset.y += ev.movementY;
                    this.drag.moved = true;
                } else {
                    this.hover = this.nodeAt(p);
                    c.style.cursor = this.hover ? "pointer" : "grab";
                }
            },
            mousedown: (ev) => {
                this.drag = { node: this.nodeAt(this.toWorld(ev)), moved: false };
            },
            mouseup: () => {
                if (this.drag && !this.drag.moved && this.drag.node) {
                    this.onOpen(this.drag.node.id);
                }
                this.drag = null;
            },
            wheel: (ev) => {
                ev.preventDefault();
                this.scale = Math.min(4, Math.max(0.2, this.scale * (ev.deltaY < 0 ? 1.1 : 0.9)));
            },
        };
        for (const [k, fn] of Object.entries(this.handlers)) {
            c.addEventListener(k, fn, { passive: k !== "wheel" });
        }
    }

    step() {
        const n = this.nodes;
        const k = 90;
        for (let i = 0; i < n.length; i++) {
            for (let j = i + 1; j < n.length; j++) {
                const dx = n[j].x - n[i].x || 0.01;
                const dy = n[j].y - n[i].y || 0.01;
                const d2 = dx * dx + dy * dy;
                if (d2 > 250000) {
                    continue;
                }
                const f = (k * k) / Math.max(d2, 1) * 0.5;
                n[i].vx -= dx * f * 0.01;
                n[i].vy -= dy * f * 0.01;
                n[j].vx += dx * f * 0.01;
                n[j].vy += dy * f * 0.01;
            }
        }
        for (const [a, b] of this.edges) {
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1;
            const f = (d - k) * 0.02;
            a.vx += (dx / d) * f;
            a.vy += (dy / d) * f;
            b.vx -= (dx / d) * f;
            b.vy -= (dy / d) * f;
        }
        for (const node of n) {
            node.vx -= node.x * 0.002;
            node.vy -= node.y * 0.002;
            if (this.drag?.node !== node) {
                node.x += node.vx * this.alpha;
                node.y += node.vy * this.alpha;
            }
            node.vx *= 0.6;
            node.vy *= 0.6;
        }
        this.alpha = Math.max(0.02, this.alpha * 0.995);
    }

    draw() {
        const ctx = this.ctx;
        const dark = document.body.classList.contains("o_dark") || matchMedia("(prefers-color-scheme: dark)").matches && false;
        ctx.clearRect(0, 0, this.w, this.h);
        ctx.save();
        ctx.translate(this.w / 2 + this.offset.x, this.h / 2 + this.offset.y);
        ctx.scale(this.scale, this.scale);
        const hl = this.hover;
        const near = new Set(hl ? this.edges.filter(([a, b]) => a === hl || b === hl).flatMap(([a, b]) => [a, b]) : []);
        for (const [a, b] of this.edges) {
            const on = hl && (a === hl || b === hl);
            ctx.strokeStyle = on ? "rgba(124,58,237,.8)" : "rgba(120,120,140,.25)";
            ctx.lineWidth = (on ? 2 : 1) / this.scale;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
        }
        for (const node of this.nodes) {
            const faded = hl && node !== hl && !near.has(node);
            ctx.globalAlpha = faded ? 0.25 : 1;
            ctx.fillStyle = node.color;
            ctx.beginPath();
            ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
            ctx.fill();
            if (node.id === this.current) {
                ctx.strokeStyle = "#111";
                ctx.lineWidth = 2 / this.scale;
                ctx.stroke();
            }
            if (this.scale > 0.6 || node === hl || node.degree > 3) {
                ctx.fillStyle = dark ? "#ddd" : "#333";
                ctx.font = `${(node === hl ? 13 : 11) / this.scale}px sans-serif`;
                ctx.textAlign = "center";
                ctx.fillText(node.name, node.x, node.y + node.r + 12 / this.scale);
            }
        }
        ctx.restore();
        ctx.globalAlpha = 1;
    }

    loop() {
        this.step();
        this.draw();
        this.raf = requestAnimationFrame(this.loop);
    }

    destroy() {
        cancelAnimationFrame(this.raf);
        for (const [k, fn] of Object.entries(this.handlers)) {
            this.canvas.removeEventListener(k, fn);
        }
    }
}

export class VaultOverview extends Component {
    static template = "wissensmanagement.VaultOverview";
    static props = { onOpen: Function, onTag: Function, currentPath: { type: [String, { value: null }], optional: true }, onClose: Function };

    setup() {
        this.orm = useService("orm");
        this.canvasRef = useRef("canvas");
        this.state = useState({ stats: null, tab: localStorage.getItem("wm_overview_tab") || "graph" });
        onWillStart(async () => {
            this.state.stats = await this.orm.call("wissen.vault", "vault_stats", []);
        });
        onMounted(() => this.mountGraph());
        onWillUnmount(() => this.graph?.destroy());
    }

    setTab(tab) {
        this.state.tab = tab;
        localStorage.setItem("wm_overview_tab", tab);
        this.graph?.destroy();
        this.graph = null;
        if (tab === "graph") {
            setTimeout(() => this.mountGraph(), 0);
        }
    }

    mountGraph() {
        if (this.state.tab !== "graph" || !this.canvasRef.el || !this.state.stats) {
            return;
        }
        this.graph = new ForceGraph(this.canvasRef.el, this.state.stats.graph, (path) => this.props.onOpen(path), this.props.currentPath);
    }

    tagSize(t) {
        return Math.min(1.6, 0.85 + t.pages.length * 0.08) + "rem";
    }
}
