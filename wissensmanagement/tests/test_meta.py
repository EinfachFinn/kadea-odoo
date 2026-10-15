import os
import shutil
import tempfile
import time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class VaultMixin:
    @classmethod
    def _setup_vault(cls):
        cls.root = tempfile.mkdtemp(prefix="wm_meta_")
        cls.env["ir.config_parameter"].sudo().set_param("wissensmanagement.vault_path", cls.root)
        cls.editor = new_test_user(cls.env, "wm_meta_editor", groups="base.group_user,wissensmanagement.group_editor")
        cls.editor2 = new_test_user(cls.env, "wm_meta_editor2", groups="base.group_user,wissensmanagement.group_editor")
        cls.reader = new_test_user(cls.env, "wm_meta_reader", groups="base.group_user,wissensmanagement.group_reader")

    def _write_file(self, rel, content):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        # eindeutige, steigende mtime in der Vergangenheit (Dateisysteme sind teils grob aufgelöst)
        VaultMixin._clock = getattr(VaultMixin, "_clock", time.time() - 10000) + 1
        os.utime(full, (VaultMixin._clock, VaultMixin._clock))

    def _read_file(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()


@tagged("post_install", "-at_install")
class TestMeta(VaultMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vault()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        for entry in os.listdir(self.root):
            p = os.path.join(self.root, entry)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        self._write_file("A.md", "# A\nLink [[B]] und [[Fehlt]] #projekt\n")
        self._write_file("B.md", "# B\n- [ ] Erledigen\n")
        self._write_file("Vertrieb/Geheim.md", "geheim")
        self.vault = self.env["wissen.vault"].with_user(self.editor)
        # Metadaten anderer Vaults (gleiche relative Pfade) dürfen die Tests nicht beeinflussen
        self.env["wissen.page"].sudo().search([]).unlink()
        self.env["wissen.access.rule"].sudo().with_context(active_test=False).search([]).unlink()

    def test_last_editor_and_versions(self):
        page = self.vault.read_page("B.md")
        res = self.vault.write_page("B.md", "# B\nneu\n", page["mtime"])
        self.assertEqual(res["meta"]["last_editor"]["id"], self.editor.id)
        versions = self.vault.list_versions("B.md")
        self.assertEqual([v["kind"] for v in versions], ["odoo", "initial"])
        # gleiche Person kurz danach -> dieselbe Version wird fortgeschrieben
        self.vault.write_page("B.md", "# B\nneuer\n", res["mtime"])
        self.assertEqual(len(self.vault.list_versions("B.md")), 2)
        # andere Person -> neue Version, "zuletzt bearbeitet von" wechselt
        vault2 = self.env["wissen.vault"].with_user(self.editor2)
        res2 = vault2.write_page("B.md", "# B\nvon zwei\n", None, True)
        self.assertEqual(res2["meta"]["last_editor"]["id"], self.editor2.id)
        self.assertEqual(len(self.vault.list_versions("B.md")), 3)
        # Wiederherstellen des Ausgangsstands
        initial = self.vault.list_versions("B.md")[-1]
        self.vault.restore_version(initial["id"])
        self.assertEqual(self._read_file("B.md"), "# B\n- [ ] Erledigen\n")

    def test_external_change_detected(self):
        self.vault.read_page("A.md")
        self._write_file("A.md", "# A\nin Obsidian geändert\n")
        meta = self.vault.read_page("A.md")["meta"]
        self.assertTrue(meta["external"])
        self.assertFalse(meta["last_editor"])
        self.assertEqual(self.vault.list_versions("A.md")[0]["kind"], "external")

    def test_presence(self):
        vault2 = self.env["wissen.vault"].with_user(self.editor2)
        vault2.heartbeat("A.md", True)
        res = self.vault.heartbeat("A.md", False)
        self.assertEqual([(o["id"], o["editing"]) for o in res["others"]], [(self.editor2.id, True)])

    def test_folder_rules(self):
        group = self.env["res.groups"].create({"name": "Vertriebsteam"})
        self.env["wissen.access.rule"].create({"name": "Vertrieb", "folder": "Vertrieb", "read_group_ids": [(6, 0, group.ids)]})
        reader = self.env["wissen.vault"].with_user(self.reader)
        self.assertNotIn("Vertrieb", [n["name"] for n in reader.get_tree()["tree"]])
        with self.assertRaises(AccessError):
            reader.read_page("Vertrieb/Geheim.md")
        self.assertFalse(reader.search("geheim"))
        self.reader.group_ids = [(4, group.id)]
        self.assertEqual(reader.read_page("Vertrieb/Geheim.md")["content"], "geheim")
        # Regel wandert beim Umbenennen des Ordners mit
        self.vault.with_user(self.env.ref("base.user_admin")).rename("Vertrieb", "Sales")
        self.assertEqual(self.env["wissen.access.rule"].search([("name", "=", "Vertrieb")]).folder, "Sales")

    def test_no_direct_orm_access_for_readers(self):
        """Leser dürfen Metadaten nur über wissen.vault sehen, sonst würden Ordnerrechte umgangen."""
        self.vault.write_page("Vertrieb/Geheim.md", "geheim v2", None, True)
        for model in ("wissen.page", "wissen.version", "wissen.comment", "wissen.mention"):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.reader).search_read([], ["id"])

    def test_rule_folder_validation(self):
        with self.assertRaises(ValidationError):
            self.env["wissen.access.rule"].create({"name": "x", "folder": "../etc"})

    def test_favorites_recent_reorder(self):
        self.assertTrue(self.vault.toggle_favorite("A.md"))
        self.vault.write_page("B.md", "x", None, True)
        data = self.vault.sidebar_data()
        self.assertEqual([f["path"] for f in data["favorites"]], ["A.md"])
        self.assertEqual(data["recent"][0]["path"], "B.md")
        self.vault.reorder("B.md", "A.md", "before")
        self.assertEqual([n["name"] for n in self.vault.get_tree()["tree"]][:2], ["B", "A"])

    def test_stats_and_record_pages(self):
        partner = self.env["res.partner"].create({"name": "Index Test"})
        self._write_file("C.md", f"[@Index Test](odoo://res.partner/{partner.id})")
        self.vault._sync_index()
        stats = self.vault.vault_stats()
        self.assertIn("projekt", [t["tag"] for t in stats["tags"]])
        self.assertIn(("A.md", "Fehlt"), [(b["path"], b["target"]) for b in stats["broken"]])
        self.assertIn(["A.md", "B.md"], stats["graph"]["edges"])
        self.assertIn("C.md", [o["path"] for o in stats["orphans"]])
        self.assertEqual([p["path"] for p in self.vault.record_pages("res.partner", partner.id)], ["C.md"])

    def test_comments(self):
        c = self.vault.add_comment("A.md", "Bitte prüfen", "Link")
        self.assertEqual(self.vault.list_comments("A.md")[0]["quote"], "Link")
        self.vault.resolve_comment(c["id"])
        self.assertEqual(self.vault.read_page("A.md")["meta"]["comment_count"], 0)
        with self.assertRaises(AccessError):
            self.env["wissen.vault"].with_user(self.editor2).delete_comment(c["id"])

    def test_create_task_from_line(self):
        if "project.task" not in self.env:
            self.skipTest("project nicht installiert")
        res = self.vault.create_task_from_line("B.md", 0, {"name": "Erledigen"})
        self.assertIn(f"(odoo://project.task/{res['task_id']})", self._read_file("B.md"))
        self.assertEqual(self.env["project.task"].browse(res["task_id"]).name, "Erledigen")

    def test_trash_restore_and_purge(self):
        self._write_file("A/Unter.md", "# Unter")
        self.vault.write_page("A.md", "# A\ngeändert\n")
        self.vault.add_comment("A.md", "Kommentar bleibt erhalten")
        self.env["wissen.trash"].sudo().search([]).unlink()
        self.vault.delete("A.md")
        self.assertFalse(os.path.exists(os.path.join(self.root, "A.md")))
        items = self.vault.list_trash()
        self.assertEqual([(i["name"], i["origin"]) for i in items], [("A", "")])
        # Leser sehen den Papierkorb nicht
        with self.assertRaises(AccessError):
            self.env["wissen.vault"].with_user(self.reader).list_trash()
        # Der Name ist inzwischen neu vergeben -> Wiederherstellen mit Nummer, Unterseiten und Kommentare kommen mit
        self._write_file("A.md", "# neues A")
        restored = self.vault.restore_trash(items[0]["id"])
        self.assertEqual(restored, "A 1.md")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "A 1/Unter.md")))
        self.assertIn("geändert", self._read_file("A 1.md"))
        self.assertEqual(self.vault.list_comments("A 1.md")[0]["body"], "Kommentar bleibt erhalten")
        self.assertFalse(self.vault.list_trash())
        # Endgültig löschen entfernt Dateien und Metadaten
        self.vault.delete("B.md")
        entry = self.vault.list_trash()[0]
        trash_path = self.env["wissen.trash"].sudo().browse(entry["id"]).trash_path
        self.vault.purge_trash(entry["id"])
        self.assertFalse(os.path.exists(os.path.join(self.root, trash_path)))
        self.assertFalse(self.env["wissen.page"].sudo().search([("path", "=", trash_path)]))
        self.assertFalse(self.vault.list_trash())

    def test_collab_session(self):
        step = {"stepType": "replace", "from": 1, "to": 1}
        join = self.vault.collab_join("B.md")
        self.assertEqual((join["version"], join["steps"]), (0, []))
        self.assertIn("Erledigen", join["base"])
        # zweite Person tritt derselben Sitzung bei
        vault2 = self.env["wissen.vault"].with_user(self.editor2)
        join2 = vault2.collab_join("B.md")
        self.assertEqual(join2["token"], join["token"])
        # Schritte werden nur auf dem neuesten Stand angenommen
        self.assertTrue(self.vault.collab_push("B.md", join["token"], 0, [step, step], "a")["ok"])
        self.assertFalse(vault2.collab_push("B.md", join["token"], 0, [step], "b")["ok"])
        pulled = vault2.collab_pull("B.md", join["token"], 0)
        self.assertEqual([s["version"] for s in pulled["steps"]], [0, 1])
        self.assertEqual(pulled["steps"][0]["client_id"], "a")
        self.assertTrue(vault2.collab_push("B.md", join["token"], 2, [step], "b")["ok"])
        self.assertEqual(vault2.collab_join("B.md")["version"], 3)
        # falsches Token -> Neustart nötig
        self.assertTrue(self.vault.collab_pull("B.md", "falsch", 0)["reset"])
        # Speichern: nur neuere Stände schreiben die Datei
        res = vault2.write_page("B.md", "# B\nStand 3\n", None, False, {"token": join["token"], "version": 3})
        self.assertFalse(res["conflict"])
        res = self.vault.write_page("B.md", "# B\nalt\n", None, False, {"token": join["token"], "version": 2})
        self.assertTrue(res.get("skipped"))
        self.assertIn("Stand 3", self._read_file("B.md"))
        # Änderung außerhalb der Sitzung wird erkannt statt überschrieben
        self._write_file("B.md", "# B\nStand 3\n- [x] im Lesemodus abgehakt\n")
        res = self.vault.write_page("B.md", "# B\nStand 4\n", None, False, {"token": join["token"], "version": 4})
        self.assertTrue(res["conflict"])
        self.assertIn("abgehakt", res["content"])
        self.assertIn("Stand 3", res["base"])
        res = self.vault.write_page("B.md", "# B\nStand 4\n- [x] im Lesemodus abgehakt\n", None, False,
                                    {"token": join["token"], "version": 5})
        self.assertFalse(res["conflict"])
        # Leser dürfen nicht mitschreiben
        with self.assertRaises(AccessError):
            self.env["wissen.vault"].with_user(self.reader).collab_join("B.md")


@tagged("post_install", "-at_install")
class TestShare(VaultMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vault()
        with open(os.path.join(cls.root, "Offen.md"), "w", encoding="utf-8") as fh:
            fh.write("# Offen\nHallo Welt ![[bild.png]]")
        os.makedirs(os.path.join(cls.root, "Anhänge"))
        with open(os.path.join(cls.root, "Anhänge", "bild.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        with open(os.path.join(cls.root, "Anhänge", "privat.png"), "wb") as fh:
            fh.write(b"\x89PNG")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        super().tearDownClass()

    def test_file_route_respects_folder_rules(self):
        os.makedirs(os.path.join(self.root, "Intern"), exist_ok=True)
        with open(os.path.join(self.root, "Intern", "plan.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        group = self.env["res.groups"].create({"name": "Nur Intern"})
        self.env["wissen.access.rule"].create({"name": "Intern", "folder": "Intern", "read_group_ids": [(6, 0, group.ids)]})
        self.authenticate("wm_meta_reader", "wm_meta_reader")
        res = self.url_open("/wissensmanagement/file?path=Intern/plan.png")
        self.assertNotEqual(res.status_code, 200)
        self.assertEqual(self.url_open("/wissensmanagement/file?path=Anhänge/bild.png").status_code, 200)

    def test_public_link(self):
        vault = self.env["wissen.vault"].with_user(self.editor)
        info = vault.set_share("Offen.md", "link")
        token = info["url"].rsplit("/", 1)[1]
        res = self.url_open(f"/wissen/s/{token}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Hallo Welt", res.text)
        self.assertTrue(res.text.lstrip().startswith("<!DOCTYPE html>"))
        self.assertEqual(self.url_open(f"/wissen/s/{token}/file?path=Anhänge/bild.png").status_code, 200)
        self.assertEqual(self.url_open(f"/wissen/s/{token}/file?path=Anhänge/privat.png").status_code, 404)
        vault.set_share("Offen.md", "off")
        self.assertEqual(self.url_open(f"/wissen/s/{token}").status_code, 404)
