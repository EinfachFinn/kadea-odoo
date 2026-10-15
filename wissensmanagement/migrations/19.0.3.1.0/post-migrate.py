"""Social-Media-Posts und -Konten auch in bestehenden Datenbanken per @ erwähnbar machen."""

NEW_MODELS = ["social.post", "social.account"]


def migrate(cr, version):
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = 'wissensmanagement.mention_models'")
    row = cr.fetchone()
    if not row or not row[0]:
        return  # nicht gesetzt: es gilt ohnehin DEFAULT_MENTION_MODELS
    models = [m.strip() for m in row[0].split(",") if m.strip()]
    missing = [m for m in NEW_MODELS if m not in models]
    if missing:
        cr.execute(
            "UPDATE ir_config_parameter SET value = %s WHERE key = 'wissensmanagement.mention_models'",
            [",".join(models + missing)],
        )
