import os
import shutil
import tempfile

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestVault(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = tempfile.mkdtemp(prefix="wm_vault_")
        cls.env["ir.config_parameter"].sudo().set_param("wissensmanagement.vault_path", cls.root)
        cls.editor = new_test_user(cls.env, "wm_editor", groups="base.group_user,wissensmanagement.group_editor")
        cls.reader = new_test_user(cls.env, "wm_reader", groups="base.group_user,wissensmanagement.group_reader")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        for entry in os.listdir(self.root):
            p = os.path.join(self.root, entry)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        self._write_file("Projekte.md", "# Projekte\n")
        self._write_file("Projekte/Alpha.md", "Siehe [[Beta]] und [[Projekte/Beta|B]].\n")
        self._write_file("Projekte/Beta.md", "# Beta\n")
        self._write_file("Vorlagen/Meeting.md", "# {{title}}\nDatum: {{date:DD.MM.YYYY}}\n")
        self._write_file("Vorlagen/Meeting/Protokoll.md", "Protokoll zu {{title}}\n")
        self._write_file(".obsidian/app.json", "{}")
        self.vault = self.env["wissen.vault"].with_user(self.editor)
        # Metadaten anderer Vaults (gleiche relative Pfade) dürfen die Tests nicht beeinflussen
        self.env["wissen.page"].sudo().search([]).unlink()
        self.env["wissen.access.rule"].sudo().with_context(active_test=False).search([]).unlink()

    def _write_file(self, rel, content):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def _read_file(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_tree_merges_page_and_folder(self):
        tree = self.vault.get_tree()
        names = [n["name"] for n in tree["tree"]]
        self.assertEqual(names, ["Projekte", "Vorlagen"])
        projekte = tree["tree"][0]
        self.assertTrue(projekte["is_page"])
        self.assertEqual(projekte["path"], "Projekte.md")
        self.assertEqual([c["name"] for c in projekte["children"]], ["Alpha", "Beta"])
        self.assertNotIn(".obsidian/app.json", tree["files"])

    def test_path_traversal_rejected(self):
        with self.assertRaises(AccessError):
            self.vault.read_page("../etc/passwd")
        with self.assertRaises(AccessError):
            self.vault.write_page("../evil.md", "x")

    def test_reader_cannot_write(self):
        reader_vault = self.env["wissen.vault"].with_user(self.reader)
        self.assertEqual(reader_vault.read_page("Projekte/Beta.md")["name"], "Beta")
        with self.assertRaises(AccessError):
            reader_vault.write_page("Projekte/Beta.md", "x")

    def test_write_conflict(self):
        page = self.vault.read_page("Projekte/Beta.md")
        os.utime(os.path.join(self.root, "Projekte/Beta.md"), (1, 1))
        res = self.vault.write_page("Projekte/Beta.md", "neu", page["mtime"])
        self.assertTrue(res["conflict"])
        res = self.vault.write_page("Projekte/Beta.md", "neu", page["mtime"], True)
        self.assertFalse(res["conflict"])
        self.assertEqual(self._read_file("Projekte/Beta.md"), "neu")

    def test_create_subpage_with_template(self):
        path = self.vault.create_page("Projekte/Beta.md", "Kickoff", "Vorlagen/Meeting.md")
        self.assertEqual(path, "Projekte/Beta/Kickoff.md")
        self.assertTrue(self._read_file(path).startswith("# Kickoff\nDatum: "))
        self.assertEqual(self._read_file("Projekte/Beta/Kickoff/Protokoll.md"), "Protokoll zu Kickoff\n")
        # Namenskollision -> Suffix
        self.assertEqual(self.vault.create_page("Projekte/Beta.md", "Kickoff"), "Projekte/Beta/Kickoff 1.md")
        with self.assertRaises(UserError):
            self.vault.create_page("", "a/b")

    def test_templates_listed(self):
        names = [t["name"] for t in self.vault.list_templates()]
        self.assertEqual(names, ["Meeting", "Meeting/Protokoll"])

    def test_rename_updates_links(self):
        res = self.vault.rename("Projekte/Beta.md", "Gamma")
        self.assertEqual(res["path"], "Projekte/Gamma.md")
        self.assertEqual(self._read_file("Projekte/Alpha.md"), "Siehe [[Gamma]] und [[Projekte/Gamma|B]].\n")

    def test_move_with_children_and_into_self(self):
        self.vault.create_page("Projekte/Beta.md", "Unter")
        res = self.vault.move("Projekte/Beta.md", "")
        self.assertEqual(res["path"], "Beta.md")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Beta/Unter.md")))
        self.assertIn("[[Beta|B]]", self._read_file("Projekte/Alpha.md"))
        with self.assertRaises(UserError):
            self.vault.move("Projekte.md", "Projekte/Alpha.md")

    def test_duplicate_and_delete(self):
        dup = self.vault.duplicate("Projekte.md")
        self.assertEqual(dup, "Projekte (Kopie).md")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Projekte (Kopie)/Alpha.md")))
        self.vault.delete("Projekte (Kopie).md")
        self.assertFalse(os.path.exists(os.path.join(self.root, "Projekte (Kopie).md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "Projekte (Kopie)")))
        self.assertEqual(len(os.listdir(os.path.join(self.root, ".trash"))), 2)

    def test_backlinks_and_search(self):
        links = self.vault.backlinks("Projekte/Beta.md")
        self.assertEqual([b["name"] for b in links], ["Alpha"])
        res = self.vault.search("beta")
        self.assertEqual(res[0]["name"], "Beta")

    def test_mentions(self):
        partner = self.env["res.partner"].create({"name": "Erika Musterfrau"})
        found = self.vault.search_mentions("Erika")
        self.assertIn(("res.partner", partner.id), [(f["model"], f["id"]) for f in found])
        self._write_file("Notiz.md", f"[@Erika](odoo://res.partner/{partner.id}) hat verhandelt.")
        self.assertEqual([m["name"] for m in self.vault.find_mentions("res.partner", partner.id)], ["Notiz"])
        partner.email = "erika@example.com"
        info = self.vault.mention_cards([["res.partner", partner.id], ["res.partner", 999999]])
        card = info[f"res.partner/{partner.id}"]
        self.assertTrue(card["exists"])
        self.assertEqual(card["name"], "Erika Musterfrau")
        self.assertTrue(card["image"].endswith("/avatar_128"))
        self.assertIn("erika@example.com", [card["subtitle"]] + [f["value"] for f in card["facts"]])
        self.assertFalse(info["res.partner/999999"]["exists"])
        # "@kontakt eri" sucht nur in Kontakten; Präfix-Treffer zuerst
        found = self.vault.search_mentions("kontakt Eri")
        self.assertTrue(found)
        self.assertEqual({f["model"] for f in found}, {"res.partner"})
        self.assertEqual(found[0]["name"], "Erika Musterfrau")

    def test_upload(self):
        path = self.vault.upload_file("Projekte.md", "bild.png", "iVBORw0KGgo=")
        self.assertEqual(path, "Anhänge/bild.png")
