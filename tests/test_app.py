import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    ValidationError,
    auth_is_configured,
    build_clear_session_cookie,
    build_session_cookie,
    create_entry,
    create_evidence,
    create_quick_log,
    create_task,
    delete_task,
    draft_cv_bullet,
    get_task,
    init_db,
    is_authenticated_cookie_header,
    is_public_path,
    list_entries,
    list_evidence,
    list_tasks,
    make_session_token,
    model_supports_reasoning,
    password_matches,
    path_requires_auth,
    update_task,
    update_entry,
    validate_session_token,
)


def memory_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


class WorkDiaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_connection()

    def tearDown(self):
        self.conn.close()

    def test_quick_log_saves_minimum_fields(self):
        entry = create_quick_log(
            self.conn,
            {
                "note": "Today I worked on FastAPI authentication and fixed a bug with JWT tokens.",
                "entry_date": "2026-06-03",
            },
        )

        self.assertEqual(entry["entry_date"], "2026-06-03")
        self.assertEqual(entry["source_mode"], "quick_log")
        self.assertIn("FastAPI authentication", entry["title"])
        self.assertEqual(entry["what_i_did"], entry["quick_note"])

    def test_required_entry_fields_are_validated(self):
        with self.assertRaises(ValidationError):
            create_entry(
                self.conn,
                {
                    "entry_date": "2026-06-03",
                    "title": "",
                    "what_i_did": "Built a small feature.",
                },
            )

    def test_evidence_links_to_entry_and_filters_by_skill(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "FastAPI authentication",
                "what_i_did": "Added JWT auth endpoints.",
                "project": "Client portal",
                "skills_used": ["FastAPI", "JWT"],
            },
        )
        evidence = create_evidence(
            self.conn,
            {
                "work_entry_id": entry["id"],
                "title": "Auth pull request",
                "evidence_type": "github",
                "evidence_url": "https://github.com/example/repo/pull/12",
                "description": "JWT auth implementation.",
            },
        )

        self.assertEqual(evidence["work_entry_id"], entry["id"])
        self.assertEqual(evidence["evidence_type"], "github")
        self.assertEqual(len(list_evidence(self.conn, {"skill": "FastAPI"})), 1)
        self.assertEqual(len(list_evidence(self.conn, {"project": "Client portal"})), 1)
        self.assertEqual(len(list_evidence(self.conn, {"skill": "React"})), 0)

    def test_evidence_model_keeps_future_attachment_metadata(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Architecture note",
                "what_i_did": "Documented storage options.",
            },
        )
        evidence = create_evidence(
            self.conn,
            {
                "work_entry_id": entry["id"],
                "title": "Upload placeholder",
                "evidence_type": "uploaded_file_placeholder",
                "evidence_url": "",
                "description": "Future uploaded file.",
                "provider": "local",
                "provider_metadata": {"original_filename": "diagram.png"},
                "storage_key": "future/uploads/diagram.png",
                "attachment_status": "placeholder",
            },
        )

        self.assertEqual(evidence["provider"], "local")
        self.assertEqual(
            evidence["provider_metadata"]["original_filename"], "diagram.png"
        )
        self.assertEqual(evidence["storage_key"], "future/uploads/diagram.png")

    def test_update_and_draft_cv_bullet(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "JWT bug fix",
                "what_i_did": "Fixed JWT expiry handling",
            },
        )

        updated = update_entry(
            self.conn,
            entry["id"],
            {
                "skills_used": "FastAPI, JWT, Pytest",
                "outcome": "Token refresh tests now pass",
            },
        )
        self.assertEqual(updated["skills_used"], ["FastAPI", "JWT", "Pytest"])

        with patch("app.read_openai_config", return_value={"api_key": ""}):
            drafted = draft_cv_bullet(self.conn, entry["id"])
        self.assertIn("using FastAPI, JWT, Pytest", drafted["cv_bullet_draft"])
        self.assertIn("resulting in token refresh tests now pass", drafted["cv_bullet_draft"])

    def test_draft_cv_bullet_uses_openai_when_key_is_configured(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Authentication hardening",
                "what_i_did": "Improved JWT validation and test coverage.",
            },
        )

        with patch(
            "app.read_openai_config",
            return_value={
                "api_key": "test-key",
                "model": "gpt-test",
                "reasoning_effort": "low",
            },
        ), patch(
            "app.generate_llm_cv_bullet",
            return_value="Strengthened JWT authentication validation and test coverage.",
        ) as generate:
            drafted = draft_cv_bullet(self.conn, entry["id"])

        self.assertEqual(
            drafted["cv_bullet_draft"],
            "Strengthened JWT authentication validation and test coverage.",
        )
        generate.assert_called_once()

    def test_list_entries_filters_by_tag(self):
        create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Debug session",
                "what_i_did": "Tracked a failing test.",
                "tags": ["debugging"],
            },
        )
        self.assertEqual(len(list_entries(self.conn, {"tag": "debugging"})), 1)
        self.assertEqual(len(list_entries(self.conn, {"tag": "frontend"})), 0)

    def test_password_auth_rejects_wrong_password(self):
        config = {
            "password": "correct horse battery staple",
            "session_secret": "test-session-secret",
            "session_seconds": 3600,
        }

        self.assertTrue(auth_is_configured(config))
        self.assertFalse(password_matches("wrong", config))
        self.assertTrue(password_matches("correct horse battery staple", config))

    def test_session_cookie_validates_signed_token(self):
        config = {
            "password": "correct horse battery staple",
            "session_secret": "test-session-secret",
            "session_seconds": 3600,
        }
        token = make_session_token(config["session_secret"], 3600, now=1000)
        live_token = make_session_token(config["session_secret"], 3600)

        self.assertTrue(validate_session_token(token, config["session_secret"], now=1200))
        self.assertFalse(validate_session_token(token, "other-secret", now=1200))
        self.assertFalse(validate_session_token(token, config["session_secret"], now=5000))
        self.assertTrue(
            is_authenticated_cookie_header(f"work_diary_session={live_token}", config)
        )

    def test_session_cookie_headers_are_secure_http_only(self):
        config = {
            "password": "correct horse battery staple",
            "session_secret": "test-session-secret",
            "session_seconds": 3600,
        }
        cookie = build_session_cookie(config)
        clear_cookie = build_clear_session_cookie()

        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Max-Age=3600", cookie)
        self.assertIn("Max-Age=0", clear_cookie)

    def test_auth_route_policy(self):
        self.assertFalse(is_public_path("/api/entries"))
        self.assertTrue(path_requires_auth("/api/entries"))
        self.assertTrue(path_requires_auth("/"))
        self.assertFalse(path_requires_auth("/api/login"))
        self.assertFalse(path_requires_auth("/api/logout"))
        self.assertFalse(path_requires_auth("/login"))
        self.assertFalse(path_requires_auth("/static/app.js"))

    def test_pwa_and_deployment_files_exist(self):
        root = Path(__file__).resolve().parents[1]

        self.assertTrue((root / "static" / "manifest.webmanifest").exists())
        self.assertTrue((root / "static" / "service-worker.js").exists())
        self.assertTrue((root / "static" / "favicon.svg").exists())
        self.assertTrue((root / "deploy" / "uk.co.workdiary.app.plist.example").exists())

    def test_reasoning_settings_only_apply_to_reasoning_models(self):
        self.assertTrue(model_supports_reasoning("gpt-5-nano"))
        self.assertTrue(model_supports_reasoning("o4-mini"))
        self.assertFalse(model_supports_reasoning("gpt-4o-mini"))
        self.assertFalse(model_supports_reasoning("gpt-4.1-nano"))

    def test_task_creation_and_open_filter(self):
        task = create_task(
            self.conn,
            {
                "title": "Finish authentication notes",
                "project": "Final Project",
                "due_date": "2026-06-04",
                "due_time": "09:30",
                "reminder_at": "2026-06-04T09:00",
                "repeat_rule": "weekly",
                "priority": "high",
                "location": "Library",
                "notes": "Bring notes from last week.",
            },
        )

        self.assertEqual(task["title"], "Finish authentication notes")
        self.assertEqual(task["project"], "Final Project")
        self.assertEqual(task["due_date"], "2026-06-04")
        self.assertEqual(task["due_time"], "09:30")
        self.assertEqual(task["reminder_at"], "2026-06-04T09:00")
        self.assertEqual(task["repeat_rule"], "weekly")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["location"], "Library")
        self.assertEqual(task["notes"], "Bring notes from last week.")
        self.assertFalse(task["completed"])
        self.assertEqual(len(list_tasks(self.conn, {"completed": "false"})), 1)
        self.assertEqual(len(list_tasks(self.conn, {"completed": "true"})), 0)

    def test_task_completion_and_delete(self):
        task = create_task(self.conn, {"title": "Log today"})
        completed = update_task(
            self.conn,
            task["id"],
            {
                "completed": True,
            },
        )

        self.assertTrue(completed["completed"])
        self.assertNotEqual(completed["completed_at"], "")
        self.assertEqual(len(list_tasks(self.conn, {"completed": "true"})), 1)

        reopened = update_task(self.conn, task["id"], {"completed": False})
        self.assertFalse(reopened["completed"])
        self.assertEqual(reopened["completed_at"], "")

        delete_task(self.conn, task["id"])
        with self.assertRaises(KeyError):
            get_task(self.conn, task["id"])

    def test_task_requires_title_and_valid_due_date(self):
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": ""})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad date", "due_date": "tomorrow"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad time", "due_time": "9am"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad reminder", "reminder_at": "soon"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad priority", "priority": "urgent"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad repeat", "repeat_rule": "yearly"})

    def test_repeating_task_creates_next_task_when_completed(self):
        task = create_task(
            self.conn,
            {
                "title": "Weekly CV review",
                "due_date": "2026-06-03",
                "due_time": "18:00",
                "reminder_at": "2026-06-03T17:30",
                "repeat_rule": "weekly",
                "priority": "medium",
            },
        )

        update_task(self.conn, task["id"], {"completed": True})
        open_tasks = list_tasks(self.conn, {"completed": "false"})

        self.assertEqual(len(open_tasks), 1)
        self.assertEqual(open_tasks[0]["title"], "Weekly CV review")
        self.assertEqual(open_tasks[0]["due_date"], "2026-06-10")
        self.assertEqual(open_tasks[0]["due_time"], "18:00")
        self.assertEqual(open_tasks[0]["reminder_at"], "2026-06-10T17:30")
        self.assertEqual(open_tasks[0]["repeat_rule"], "weekly")


if __name__ == "__main__":
    unittest.main()
