import tempfile
import unittest
from pathlib import Path

import web_server


class LocalWebControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = unittest.mock.patch.object(
            web_server, "DB_PATH", Path(self.temp.name) / "ui-test.db"
        )
        self.db_patch.start()
        web_server.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def handler(self):
        instance = object.__new__(web_server.Handler)
        instance.responses = []
        instance._json = lambda status, payload: instance.responses.append((status, payload))
        return instance

    def add_pair(self, prompt, answer="A useful answer."):
        with web_server.database() as con:
            cid = con.execute("SELECT value FROM settings WHERE key='active_conversation'").fetchone()[0]
            user_id = con.execute(
                "INSERT INTO messages(conversation_id,role,content,created) VALUES(?,?,?,?)",
                (cid, "user", prompt, web_server.utc_now()),
            ).lastrowid
            assistant_id = con.execute(
                "INSERT INTO messages(conversation_id,role,content,provider,created) VALUES(?,?,?,?,?)",
                (cid, "assistant", answer, "Groq", web_server.utc_now()),
            ).lastrowid
        return user_id, assistant_id

    def test_training_candidate_selection_is_atomic_and_owner_initiated(self):
        user_id, assistant_id = self.add_pair("Keep answers concise.")
        handler = self.handler()
        handler._create_candidate({"message_id": assistant_id, "category": "preference"})
        self.assertEqual(handler.responses[0][0], 201)
        with web_server.database() as con:
            flags = con.execute(
                "SELECT excluded_training FROM messages WHERE id IN (?,?) ORDER BY id",
                (user_id, assistant_id),
            ).fetchall()
            candidates = con.execute("SELECT COUNT(*) FROM training_examples").fetchone()[0]
        self.assertEqual([row[0] for row in flags], [0, 0])
        self.assertEqual(candidates, 1)

    def test_secret_candidate_is_rejected_without_changing_training_flags(self):
        _, assistant_id = self.add_pair("api_key='abcdefghijklmno'")
        handler = self.handler()
        handler._create_candidate({"message_id": assistant_id})
        self.assertEqual(handler.responses[0][0], 400)
        with web_server.database() as con:
            flags = [row[0] for row in con.execute(
                "SELECT excluded_training FROM messages ORDER BY id"
            ).fetchall()]
            candidates = con.execute("SELECT COUNT(*) FROM training_examples").fetchone()[0]
        self.assertEqual(flags, [1, 1])
        self.assertEqual(candidates, 0)

    def test_memory_delete_removes_saved_fact(self):
        with web_server.database() as con:
            memory_id = con.execute(
                "INSERT INTO memories(content,category,source_type,confidence,created,updated) VALUES(?,?,?,?,?,?)",
                ("Temporary test fact", "test", "confirmed", 1.0,
                 web_server.utc_now(), web_server.utc_now()),
            ).lastrowid
        handler = self.handler()
        handler._update_memory(memory_id, {})
        self.assertEqual(handler.responses[0][0], 200)
        with web_server.database() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone())


if __name__ == "__main__":
    unittest.main()
