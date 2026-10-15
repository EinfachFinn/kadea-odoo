"""Freigegebene Seiten (öffentlicher Link, Kundenportal) und Druck-/PDF-Ansicht.

Gerendert wird im Browser mit marked + DOMPurify (dieselben Bibliotheken wie im Backend).
@-Erwähnungen werden dort nur als Name angezeigt, es werden keine Odoo-Daten nach außen gegeben.
Die HTML-Seiten sind QWeb-Templates (views/share_templates.xml).
"""

import mimetypes
import os
import re

from markupsafe import Markup

from odoo import http
from odoo.http import request

from .main import INLINE_TYPES


class WissenShareController(http.Controller):

    def _style(self):
        icp = request.env["ir.config_parameter"].sudo()

        def get(key, default):
            value = icp.get_param("wissensmanagement." + key, default) or default
            # nur Farbwerte/Zahlen zulassen, da sie in ein style-Attribut wandern
            return value if re.fullmatch(r"#[0-9A-Fa-f]{3,8}|\d{2,4}", str(value)) else default

        return {
            "accent": get("accent_color", "#714B67"),
            "wikilink": get("wikilink_color", "#7c3aed"),
            "mention": get("mention_color", "#0e7490"),
            "width": get("content_width", "860"),
        }

    def _render(self, payload, header, back_url="", back_label="", print_=False):
        vault = request.env["wissen.vault"].sudo()
        payload["print"] = print_
        return request.render("wissensmanagement.share_page", {
            "title": payload["title"],
            "header": header,
            "back_url": back_url,
            "back_label": back_label,
            "payload": payload,
            "payload_json": Markup(vault._json(payload)),
            "style": self._style(),
        }, headers={"X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})

    def _serve_file(self, page, path):
        vault = request.env["wissen.vault"].sudo()
        payload = vault._shared_payload(page)
        if path not in payload["files"]:
            raise request.not_found()
        full = vault._abs(path)
        if not os.path.isfile(full):
            raise request.not_found()
        mimetype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        inline = mimetype in INLINE_TYPES
        return request.make_response(data, headers=[
            ("Content-Type", mimetype if inline else "application/octet-stream"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Disposition", http.content_disposition(os.path.basename(full), "inline" if inline else "attachment")),
        ])

    # --- öffentlicher Link -------------------------------------------------
    @http.route("/wissen/s/<string:token>", type="http", auth="public", methods=["GET"], readonly=True)
    def shared_page(self, token, **kw):
        vault = request.env["wissen.vault"].sudo()
        page = vault._shared_page(token)
        if not page or page.share_mode != "link":
            raise request.not_found()
        payload = vault._shared_payload(page)
        payload.update(pageBase="/wissen/s/", fileBase=f"/wissen/s/{token}/file?path=")
        return self._render(payload, request.env._("Geteilte Wissensseite"))

    @http.route("/wissen/s/<string:token>/file", type="http", auth="public", methods=["GET"], readonly=True)
    def shared_file(self, token, path="", **kw):
        page = request.env["wissen.vault"].sudo()._shared_page(token)
        if not page or page.share_mode != "link":
            raise request.not_found()
        return self._serve_file(page, path)

    # --- Kundenportal ------------------------------------------------------
    @http.route("/my/wissen", type="http", auth="user", methods=["GET"], readonly=True)
    def portal_list(self, **kw):
        partner = request.env.user.partner_id
        Page = request.env["wissen.page"].sudo()
        pages = Page.search([("share_mode", "=", "portal"), ("share_token", "!=", False)])
        vault = request.env["wissen.vault"].sudo()
        pages = pages.filtered(lambda p: vault._shared_page(p.share_token, partner))
        return request.render("wissensmanagement.portal_list", {
            "title": request.env._("Wissen"),
            "header": request.env._("Für dich freigegebene Wissensseiten"),
            "back_url": "/my",
            "back_label": request.env._("Zum Portal"),
            "pages": pages,
            "style": self._style(),
        })

    @http.route("/my/wissen/<string:token>", type="http", auth="user", methods=["GET"], readonly=True)
    def portal_page(self, token, **kw):
        vault = request.env["wissen.vault"].sudo()
        page = vault._shared_page(token, request.env.user.partner_id)
        if not page or page.share_mode != "portal":
            raise request.not_found()
        payload = vault._shared_payload(page)
        payload.update(pageBase="/my/wissen/", fileBase=f"/my/wissen/{token}/file?path=")
        return self._render(payload, request.env._("Wissen"), "/my/wissen", request.env._("Alle Seiten"))

    @http.route("/my/wissen/<string:token>/file", type="http", auth="user", methods=["GET"], readonly=True)
    def portal_file(self, token, path="", **kw):
        page = request.env["wissen.vault"].sudo()._shared_page(token, request.env.user.partner_id)
        if not page or page.share_mode != "portal":
            raise request.not_found()
        return self._serve_file(page, path)

    # --- Druck / PDF (intern) ---------------------------------------------
    @http.route("/wissensmanagement/print", type="http", auth="user", methods=["GET"], readonly=True)
    def print_page(self, path, **kw):
        vault = request.env["wissen.vault"]
        vault._check_read()
        root = vault._root()
        full = vault._abs(path, root)
        rel = vault._rel(full, root)
        vault._check_path(rel)
        if not os.path.isfile(full):
            raise request.not_found()
        with open(full, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        files = [vault._rel(f, root) for f in vault._walk_files(root)]
        page = request.env["wissen.page"].sudo().search([("path", "=", rel)], limit=1)
        payload = {
            "title": os.path.basename(rel).removesuffix(".md"), "content": content, "links": {},
            "files": [f for f in files if not f.endswith(".md")], "pages": [],
            "updated": str(page.last_edit_date or "") if page else "", "editor": page.last_editor_id.name or "" if page else "",
            "pageBase": "#", "fileBase": "/wissensmanagement/file?path=",
        }
        return self._render(payload, request.env._("Druckansicht"), print_=True)
