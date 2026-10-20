from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devil import file_ops, indexer, pathindex, policy, search
from devil.agent import DevilAgent, run_local_tool
from devil.jobs import IndexJobManager, index_projects


class FreshSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test-index.db"
        self.path = self.root / "notes.py"
        self.path.write_text("obstacle detector implementation", encoding="utf-8")
        self.db_patch = patch.object(indexer, "DB_PATH", self.db)
        self.roots_patch = patch.object(policy, "load_roots", return_value=[self.root])
        self.db_patch.start()
        self.roots_patch.start()
        con = indexer.connect()
        try:
            indexer.index_file(con, self.path)
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        self.roots_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_search_refreshes_changed_file_before_returning_excerpt(self):
        self.path.write_text("unrelated new material", encoding="utf-8")

        self.assertEqual(search.search_files("obstacle"), [])
        results = search.search_files("unrelated")

        self.assertEqual(len(results), 1)
        self.assertIn("unrelated", results[0]["excerpt"])

    def test_search_removes_missing_file_from_index(self):
        self.path.unlink()

        self.assertEqual(search.search_files("obstacle"), [])
        con = indexer.connect()
        try:
            count = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 0)

    def test_search_does_not_return_files_outside_current_roots(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with patch.object(policy, "load_roots", return_value=[elsewhere]):
            self.assertEqual(search.search_files("obstacle"), [])


class RoutingTests(unittest.TestCase):
    def make_agent(self, history):
        agent = DevilAgent.__new__(DevilAgent)
        agent.groq = object()
        agent.gemini = object()
        agent.openrouter = None
        agent.history = history
        agent._last_files = []
        agent.ask_groq = lambda message, concise=False: ("Groq handled it", False)
        agent.ask_gemini = lambda message, concise=False: ("Gemini handled it", False)
        return agent

    def test_short_technical_followup_stays_with_groq(self):
        agent = self.make_agent([
            {"role": "user", "content": "Write the code for insertion sort."},
            {"role": "assistant", "content": "The sort function is ready.",
             "provider": "Groq"},
        ])

        provider, answer = agent.ask("Make it take an array of ten elements.")

        self.assertEqual(provider, "Groq")
        self.assertEqual(answer, "Groq handled it")

    def test_explicit_file_action_overrides_recent_gemini_turn(self):
        agent = self.make_agent([
            {"role": "assistant", "content": "I cannot modify local files.",
             "provider": "Gemini"},
        ])

        provider, answer = agent.ask("Modify insertion.py to read ten values.")

        self.assertEqual(provider, "Groq")
        self.assertEqual(answer, "Groq handled it")

    def test_local_file_task_is_not_falsely_fallbacked_to_gemini(self):
        agent = self.make_agent([])
        agent.ask_groq = lambda message, concise=False: (_ for _ in ()).throw(
            RuntimeError("Groq unavailable")
        )
        gemini_calls = []
        agent.ask_gemini = lambda message, concise=False: gemini_calls.append(message)

        provider, answer = agent.ask("Modify insertion.py to read ten values.")

        self.assertEqual(provider, "Unavailable")
        self.assertIn("Groq unavailable", answer)
        self.assertEqual(gemini_calls, [])

    def test_provider_label_identifies_gemini_tool_result_synthesis(self):
        agent = self.make_agent([])

        def groq_with_gemini_synthesis(_message, concise=False):
            agent._last_synth_provider = "gemini"
            return "Answer assembled from local evidence", False

        agent.ask_groq = groq_with_gemini_synthesis
        provider, _ = agent.ask("Explain this function.")

        self.assertEqual(provider, "Groq tools + Gemini synthesis")
        self.assertEqual(agent.history[-1]["provider"], "Groq")
        self.assertEqual(agent.history[-1]["synthesis_provider"], "gemini")


class IncrementalPathIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(pathindex, "DB_PATH", self.root / "paths.db")
        self.roots_patch = patch.object(policy, "load_roots", return_value=[self.root])
        self.db_patch.start()
        self.roots_patch.start()

    def tearDown(self):
        self.roots_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_incremental_scan_updates_renames_and_removes_stale_names(self):
        original = self.root / "alpha.py"
        original.write_text("pass", encoding="utf-8")
        pathindex.scan()
        self.assertTrue(pathindex.find("alpha", kind="file")["results"])

        original.rename(self.root / "beta.py")
        pathindex.scan()

        self.assertEqual(pathindex.find("alpha", kind="file")["results"], [])
        self.assertTrue(pathindex.find("beta", kind="file")["results"])

    def test_partial_root_scan_keeps_previous_entries_and_reports_failure(self):
        target = self.root / "kept.py"
        target.write_text("pass", encoding="utf-8")
        pathindex.scan()

        with patch("devil.pathindex.os.scandir", side_effect=PermissionError("offline")):
            result = pathindex.scan()

        self.assertEqual(len(result["inaccessible"]), 1)
        self.assertTrue(pathindex.find("kept", kind="file")["results"])

    def test_filename_results_respect_current_allowed_roots(self):
        (self.root / "private-project.py").write_text("pass", encoding="utf-8")
        pathindex.scan()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()

        with patch.object(policy, "load_roots", return_value=[elsewhere]):
            self.assertEqual(
                pathindex.find("private-project", kind="file")["results"], []
            )

class SafeEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "sample.py"
        self.action_db_patch = patch.object(file_ops, "ACTION_DB", self.root / "actions.db")
        self.action_db_patch.start()
        self.roots_patch = patch.object(policy, "load_roots", return_value=[self.root])
        self.roots_patch.start()

    def tearDown(self):
        self.roots_patch.stop()
        self.action_db_patch.stop()
        self.temp.cleanup()

    def test_edit_preserves_crlf_when_model_supplies_lf(self):
        self.path.write_bytes(b"first\r\nold line\r\nlast\r\n")
        with patch("devil.file_ops._confirm", return_value=True):
            result = file_ops.edit_file({
                "path": str(self.path), "old_text": "old line\nlast",
                "new_text": "new line\nlast",
            })

        self.assertEqual(result["replacements"], 1)
        self.assertEqual(self.path.read_bytes(), b"first\r\nnew line\r\nlast\r\n")
        self.assertEqual(Path(result["backup"]).read_bytes(),
                         b"first\r\nold line\r\nlast\r\n")

    def test_edit_aborts_if_file_changes_during_approval(self):
        self.path.write_text("before", encoding="utf-8")

        def change_while_approving(_action):
            self.path.write_text("concurrent update", encoding="utf-8")
            return True

        with patch("devil.file_ops._confirm", side_effect=change_while_approving):
            result = file_ops.edit_file({
                "path": str(self.path), "old_text": "before", "new_text": "after",
            })

        self.assertIn("changed while waiting", result["error"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "concurrent update")

    def test_delete_is_quarantined_and_undo_restores_original(self):
        self.path.write_text("keep me", encoding="utf-8")
        with patch("devil.file_ops._confirm", return_value=True):
            deleted = file_ops.delete_file({"path": str(self.path)})
        self.assertTrue(deleted["recoverable"])
        self.assertFalse(self.path.exists())
        self.assertEqual(Path(deleted["recovery_path"]).read_text(encoding="utf-8"), "keep me")
        with patch("devil.file_ops._confirm", return_value=True):
            undone = file_ops.undo_last()
        self.assertEqual(undone["undone"], "delete")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "keep me")

    def test_edit_can_be_undone_but_refuses_newer_content(self):
        self.path.write_text("old", encoding="utf-8")
        with patch("devil.file_ops._confirm", return_value=True):
            file_ops.edit_file({"path": str(self.path), "old_text": "old", "new_text": "new"})
            undone = file_ops.undo_last()
        self.assertEqual(undone["undone"], "edit")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old")

    def test_background_job_returns_immediately_and_supports_cancellation(self):
        manager = IndexJobManager()
        entered, release = __import__("threading").Event(), __import__("threading").Event()
        def long_job(cancel, progress):
            entered.set()
            progress({"scanned": 1})
            release.wait(2)
            return {"cancelled": cancel.is_set()}
        started = manager.start("test", long_job)
        self.assertIn(started["status"], {"queued", "running"})
        self.assertTrue(entered.wait(1))
        self.assertTrue(manager.cancel()["cancel_requested"])
        release.set()
        manager._job["thread"].join(1)
        self.assertEqual(manager.snapshot()["status"], "cancelled")


class LocalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "vision-project"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text("[project]\nname='vision'\n")
        self.path = self.project / "detector.py"
        self.path.write_text("def detect():\n    return 'old detector'\n", encoding="utf-8")
        self.patches = [
            patch.object(indexer, "DB_PATH", self.root / "content.db"),
            patch.object(pathindex, "DB_PATH", self.root / "paths.db"),
            patch.object(file_ops, "ACTION_DB", self.root / "actions.db"),
            patch.object(policy, "load_roots", return_value=[self.root]),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_simulated_voice_transcript_find_read_edit_validate_undo(self):
        # Tests the post-transcription action path only; no mic, ASR, speaker,
        # or provider API is used.
        pathindex.scan()
        indexer.index_folder(self.project)
        results = run_local_tool("search_local_files", {"query": "old detector"})
        self.assertTrue(results["results"])
        self.assertEqual(Path(results["results"][0]["path"]), self.path)
        read = run_local_tool("read_indexed_file", {"path": str(self.path)})
        self.assertIn("old detector", read["content"])
        with patch("devil.file_ops._confirm", return_value=True):
            edited = file_ops.edit_file({"path": str(self.path),
                                         "old_text": "old detector",
                                         "new_text": "improved detector"})
        self.assertTrue(edited.get("backup"))
        validated = search.search_files("improved detector")
        self.assertTrue(validated)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("improved detector"), 1)
        with patch("devil.file_ops._confirm", return_value=True):
            undone = file_ops.undo_last()
        self.assertEqual(undone["undone"], "edit")
        self.assertIn("old detector", self.path.read_text(encoding="utf-8"))

    def test_large_project_scan_respects_file_cap(self):
        for i in range(30):
            (self.project / f"module_{i}.py").write_text(f"# entry {i}\n", encoding="utf-8")
        result = indexer.index_folder(self.project, max_files=5)
        self.assertEqual(result["scanned"], 5)
        self.assertTrue(result["capped"])

    def test_project_index_job_rotates_cursor_between_capped_passes(self):
        for name in ("alpha", "bravo", "charlie"):
            project = self.root / name
            project.mkdir()
            (project / "package.json").write_text("{}", encoding="utf-8")
            for i in range(3):
                (project / f"module_{i}.py").write_text(f"# {name} {i}\n", encoding="utf-8")
        pathindex.scan()
        first = index_projects(__import__("threading").Event(), lambda _p: None,
                               max_files=4, max_files_per_project=2)
        second = index_projects(__import__("threading").Event(), lambda _p: None,
                                max_files=4, max_files_per_project=2)
        self.assertEqual(first["scanned"], 4)
        self.assertEqual(second["scanned"], 4)
        self.assertGreaterEqual(first["projects_capped"], 1)
        self.assertGreaterEqual(second["projects_capped"], 1)

if __name__ == "__main__":
    unittest.main()
