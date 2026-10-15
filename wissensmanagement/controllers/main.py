import mimetypes
import os

from odoo import http
from odoo.http import request

# Inline-Anzeige nur für unkritische Typen; alles andere (z. B. HTML, SVG mit Skripten) als Download.
INLINE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif",
    "application/pdf", "audio/mpeg", "audio/ogg", "audio/wav", "video/mp4", "video/webm", "text/plain",
}


class WissensmanagementController(http.Controller):

    @http.route("/wissensmanagement/file", type="http", auth="user", methods=["GET"], readonly=True)
    def vault_file(self, path, download=False, **kwargs):
        vault = request.env["wissen.vault"]
        vault._check_read()
        full = vault._abs(path)
        vault._check_path(vault._rel(full))  # Ordnerrechte gelten auch für Bilder/Anhänge
        if not os.path.isfile(full):
            raise request.not_found()
        mimetype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        filename = os.path.basename(full)
        inline = mimetype in INLINE_TYPES and not download
        headers = [
            ("Content-Type", mimetype if inline else "application/octet-stream"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "private, max-age=60"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Disposition", http.content_disposition(filename, "inline" if inline else "attachment")),
        ]
        return request.make_response(data, headers=headers)
