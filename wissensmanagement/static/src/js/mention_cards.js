// Live-Vorschaukarten für @-Erwähnungen von Odoo-Datensätzen.
// Die Daten kommen bei jedem Anzeigen frisch aus Odoo (Phase, Zuständige, Deadline …),
// werden aber pro Sitzung kurz zwischengespeichert, damit viele Erwähnungen nur einen Request kosten.
import { escapeHtml } from "./markdown";

const CACHE_MS = 30000;
const cache = new Map(); // key -> { at, promise }
let pending = null; // gebündelte Anfrage

export function mentionKey(model, id) {
    return `${model}/${id}`;
}

export function invalidateCards() {
    cache.clear();
}

/** Lädt Karten gebündelt (alle Aufrufe im selben Tick -> ein ORM-Call). */
export function loadCard(orm, model, id) {
    const key = mentionKey(model, id);
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < CACHE_MS) {
        return hit.promise;
    }
    if (!pending) {
        const batch = { refs: [], resolvers: {} };
        pending = batch;
        batch.promise = new Promise((resolve) => setTimeout(resolve, 0)).then(() => {
            pending = null;
            return orm.call("wissen.vault", "mention_cards", [batch.refs]);
        });
    }
    const batch = pending;
    batch.refs.push([model, parseInt(id, 10)]);
    const promise = batch.promise.then((res) => res[key] || { exists: false }).catch(() => ({ exists: false }));
    cache.set(key, { at: Date.now(), promise });
    return promise;
}

function formatDate(iso) {
    if (!iso) {
        return "";
    }
    const [y, m, d] = iso.slice(0, 10).split("-");
    return `${d}.${m}.${y}`;
}

function avatar(card, size = "") {
    if (card.image) {
        return `<img class="o_wm_card_avatar ${size}" src="${escapeHtml(card.image)}" alt=""/>`;
    }
    return `<span class="o_wm_card_avatar o_wm_card_icon ${size}"><i class="fa ${escapeHtml(card.icon || "fa-database")}"></i></span>`;
}

function badgeClass(badge) {
    const v = (badge.label || "").toLowerCase();
    if (/erledigt|done|gewonnen|won|bestätigt|confirmed|bezahlt|paid|gebucht|posted|abgeschlossen/.test(v)) {
        return "o_wm_badge_success";
    }
    if (/abgebrochen|storniert|cancel|verloren|lost|blockiert|blocked|gefährdet|risk/.test(v)) {
        return "o_wm_badge_danger";
    }
    if (/entwurf|draft|neu|new|angebot|quotation/.test(v)) {
        return "o_wm_badge_muted";
    }
    return "";
}

function stars(priority) {
    if (!priority) {
        return "";
    }
    let html = '<span class="o_wm_card_stars">';
    for (let i = 1; i <= priority.max; i++) {
        html += `<i class="fa ${i <= priority.value ? "fa-star" : "fa-star-o"}"></i>`;
    }
    return html + "</span>";
}

/** Große Karte (Erwähnung allein in einer Zeile oder Hover-Vorschau). */
export function cardHtml(card) {
    if (!card || !card.exists) {
        return `<span class="o_wm_card o_wm_card_missing"><i class="fa fa-chain-broken me-2"></i>Datensatz existiert nicht mehr</span>`;
    }
    if (card.restricted) {
        return `<span class="o_wm_card o_wm_card_missing"><i class="fa fa-lock me-2"></i>${escapeHtml(card.label)} – kein Zugriff</span>`;
    }
    const badges = (card.badges || [])
        .map((b) => `<span class="o_wm_badge ${badgeClass(b)}">${escapeHtml(b.label)}</span>`)
        .join("");
    const tags = (card.tags || [])
        .map((t) => `<span class="o_tag badge rounded-pill o_tag_color_${t.color % 12}">${escapeHtml(t.name)}</span>`)
        .join("");
    const facts = (card.facts || [])
        .map((f) => `<span class="o_wm_card_fact"><span class="text-muted">${escapeHtml(f.label)}:</span> ${escapeHtml(f.type === "date" ? formatDate(f.value) : f.value)}</span>`)
        .join("");
    const users = (card.users || [])
        .map((u) => `<img class="o_wm_card_user" src="${escapeHtml(u.image)}" title="${escapeHtml(u.name)}" alt=""/>`)
        .join("");
    const deadline = card.deadline
        ? `<span class="o_wm_card_deadline ${card.overdue ? "text-danger" : ""}"><i class="fa fa-clock-o me-1"></i>${formatDate(card.deadline)}</span>`
        : "";
    return (
        `<span class="o_wm_card${card.active === false ? " o_wm_card_archived" : ""}">` +
        avatar(card, "o_wm_card_avatar_lg") +
        `<span class="o_wm_card_body">` +
        `<span class="o_wm_card_type"><i class="fa ${escapeHtml(card.icon)} me-1"></i>${escapeHtml(card.label)}</span>` +
        `<span class="o_wm_card_title">${escapeHtml(card.name)}</span>` +
        (card.subtitle ? `<span class="o_wm_card_sub">${escapeHtml(card.subtitle)}</span>` : "") +
        (badges || tags ? `<span class="o_wm_card_row">${badges}${tags}</span>` : "") +
        (facts ? `<span class="o_wm_card_facts">${facts}</span>` : "") +
        (deadline || users || card.priority
            ? `<span class="o_wm_card_foot">${deadline}${stars(card.priority)}<span class="o_wm_card_users">${users}</span></span>`
            : "") +
        `</span></span>`
    );
}

/** Inhalt eines kompakten Chips im Fließtext. */
export function chipHtml(card, fallbackName) {
    if (!card || !card.exists) {
        return `<i class="fa fa-chain-broken me-1"></i>${escapeHtml(fallbackName)}`;
    }
    const name = card.restricted ? fallbackName : card.name;
    const img = card.image
        ? `<img class="o_wm_chip_avatar" src="${escapeHtml(card.image)}" alt=""/>`
        : `<i class="fa ${escapeHtml(card.icon || "fa-database")} me-1"></i>`;
    const stage = card.badges?.[0] ? `<span class="o_wm_chip_stage">${escapeHtml(card.badges[0].label)}</span>` : "";
    return `${img}${escapeHtml(name)}${stage}`;
}

/** Steht das Element allein in seiner Zeile (nur <br> oder Absatzgrenzen drumherum)? */
function isAloneOnLine(el) {
    const blank = (n) => n && n.nodeType === Node.TEXT_NODE && !n.textContent.trim();
    let prev = el.previousSibling;
    while (blank(prev)) {
        prev = prev.previousSibling;
    }
    let next = el.nextSibling;
    while (blank(next)) {
        next = next.nextSibling;
    }
    return (!prev || prev.nodeName === "BR") && (!next || next.nodeName === "BR");
}

/**
 * Wertet ein Erwähnungs-Element auf: allein in der Zeile -> große Karte, sonst Chip mit Hover-Vorschau.
 * @param {HTMLElement} el Element mit data-wm-model / data-wm-id
 */
export async function decorateMention(orm, el, { allowBlock = true } = {}) {
    const { wmModel, wmId } = el.dataset;
    const fallback = el.textContent.replace(/^@/, "");
    const card = await loadCard(orm, wmModel, wmId);
    if (!el.isConnected) {
        return;
    }
    const parent = el.parentElement;
    const alone = allowBlock && parent && ["P", "LI", "TD"].includes(parent.tagName) && isAloneOnLine(el);
    if (alone) {
        el.classList.add("o_wm_mention_block");
        el.innerHTML = cardHtml(card);
        parent.classList.add("o_wm_card_paragraph");
    } else {
        el.innerHTML = chipHtml(card, fallback) + `<span class="o_wm_mention_pop">${cardHtml(card)}</span>`;
    }
    el.classList.toggle("o_wm_mention_missing", !card.exists);
}
