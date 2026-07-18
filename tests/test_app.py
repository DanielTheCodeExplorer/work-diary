import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    MAX_ACHIEVEMENT_BULLETS,
    ValidationError,
    auth_is_configured,
    build_clear_session_cookie,
    build_session_cookie,
    build_daily_summary_payload,
    complete_project,
    create_entry,
    create_evidence,
    create_image_evidence,
    create_project,
    create_quick_log,
    create_task,
    delete_entry,
    delete_project,
    delete_push_subscription,
    delete_task,
    draft_cv_bullet,
    extract_achievement_bullets,
    extract_openai_lines,
    get_push_status,
    get_task,
    get_bootstrap,
    google_calendar_event_body,
    google_task_body,
    google_task_sync_target,
    init_db,
    is_authenticated_cookie_header,
    is_public_path,
    list_entries,
    list_evidence,
    list_achievements,
    list_projects,
    list_push_subscriptions,
    list_tasks,
    make_session_token,
    model_supports_reasoning,
    password_matches,
    path_requires_auth,
    reorder_project_tasks,
    save_push_subscription,
    sync_task_reminder_schedules,
    task_reminder_datetime,
    suggest_project_next_steps,
    sync_task_to_google,
    update_task,
    update_entry,
    update_project,
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
        self.openai_config_patch = patch(
            "app.read_openai_config",
            return_value={
                "api_key": "",
                "model": "gpt-5.4-nano",
                "reasoning_effort": "low",
            },
        )
        self.openai_config_patch.start()
        self.conn = memory_connection()

    def tearDown(self):
        self.conn.close()
        self.openai_config_patch.stop()

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

    def test_image_evidence_creates_entry_and_evidence_record(self):
        with tempfile.TemporaryDirectory() as uploads_dir, patch(
            "app.UPLOADS_DIR", Path(uploads_dir)
        ):
            payload = create_image_evidence(
                self.conn,
                {
                    "data_url": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                    "filename": "site-photo.jpg",
                    "comment": "Whiteboard notes from the planning session.",
                    "entry_date": "2026-06-03",
                    "project": "Client portal",
                },
            )

        evidence = payload["evidence"]
        self.assertEqual(payload["entry"]["project"], "Client portal")
        self.assertEqual(evidence["evidence_type"], "image")
        self.assertEqual(evidence["description"], "Whiteboard notes from the planning session.")
        self.assertEqual(evidence["provider"], "local")
        self.assertEqual(evidence["attachment_status"], "uploaded")
        self.assertEqual(evidence["provider_metadata"]["content_type"], "image/jpeg")

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

    def test_achievements_table_is_created(self):
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(achievements)").fetchall()
        }

        self.assertIn("source_entry_id", columns)
        self.assertIn("achieved_at", columns)
        self.assertIn("bullet", columns)

    def test_projects_table_is_created(self):
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(projects)").fetchall()
        }

        self.assertIn("name", columns)
        self.assertIn("goal", columns)
        self.assertIn("deadline", columns)
        self.assertIn("status", columns)
        self.assertIn("completed_at", columns)

    def test_tasks_store_project_order_for_next_steps(self):
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(tasks)").fetchall()
        }

        self.assertIn("project_order", columns)

    def test_project_create_link_and_bootstrap_backfill(self):
        task = create_task(
            self.conn,
            {
                "title": "Build dashboard",
                "project": "Client portal",
            },
        )
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Dashboard progress",
                "what_i_did": "Built summary widgets.",
                "project": "Client portal",
            },
        )

        payload = get_bootstrap(self.conn)

        self.assertEqual(len(payload["projects"]), 1)
        self.assertEqual(payload["projects"][0]["name"], "Client portal")
        self.assertEqual(payload["tasks"][0]["project_id"], str(payload["projects"][0]["id"]))
        self.assertEqual(payload["entries"][0]["project_id"], str(payload["projects"][0]["id"]))
        self.assertEqual(task["project"], "Client portal")
        self.assertEqual(entry["project"], "Client portal")

    def test_project_crud_and_optional_links_for_tasks_and_cv_notes(self):
        project = create_project(
            self.conn,
            {
                "name": "Portfolio rebuild",
                "goal": "Ship a cleaner portfolio.",
                "deadline": "2026-07-01",
                "status": "active",
                "color": "#73A7F2",
            },
        )
        task = create_task(
            self.conn,
            {
                "title": "Draft case study",
                "project_id": project["id"],
            },
        )
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-04",
                "title": "Case study outline",
                "what_i_did": "Outlined the portfolio case study.",
                "project_id": project["id"],
            },
        )

        self.assertEqual(task["project"], "Portfolio rebuild")
        self.assertEqual(entry["project"], "Portfolio rebuild")

        renamed = update_project(
            self.conn,
            project["id"],
            {
                "name": "Portfolio launch",
                "status": "paused",
            },
        )

        self.assertEqual(renamed["name"], "Portfolio launch")
        self.assertEqual(get_task(self.conn, task["id"])["project"], "Portfolio launch")
        self.assertEqual(list_entries(self.conn)[0]["project"], "Portfolio launch")

        delete_project(self.conn, project["id"])
        self.assertEqual(get_task(self.conn, task["id"])["project_id"], "")
        self.assertEqual(list_entries(self.conn)[0]["project_id"], "")
        self.assertEqual(list_projects(self.conn), [])

    def test_project_suggestions_fallback_returns_max_three_items(self):
        project = create_project(
            self.conn,
            {
                "name": "Research app",
                "goal": "Plan a small research tracker.",
                "deadline": "2026-06-30",
                "status": "active",
            },
        )
        create_task(self.conn, {"title": "Collect requirements", "project_id": project["id"]})

        suggestions = suggest_project_next_steps(self.conn, project["id"])

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertLessEqual(len(suggestions), 3)
        self.assertIn("title", suggestions[0])
        self.assertIn("guidance", suggestions[0])

    def test_project_task_order_is_assigned_and_can_be_reordered(self):
        project = create_project(
            self.conn,
            {
                "name": "Animation",
                "goal": "Plan animation work.",
                "status": "active",
            },
        )
        first = create_task(self.conn, {"title": "Storyboard", "project_id": project["id"]})
        second = create_task(self.conn, {"title": "Animate intro", "project_id": project["id"]})
        third = create_task(self.conn, {"title": "Export draft", "project_id": project["id"]})

        self.assertEqual(first["project_order"], 10)
        self.assertEqual(second["project_order"], 20)
        self.assertEqual(third["project_order"], 30)

        result = reorder_project_tasks(
            self.conn,
            project["id"],
            {"task_ids": [str(third["id"]), str(first["id"]), str(second["id"])]},
        )

        ordered_titles = sorted(
            result["tasks"],
            key=lambda task: task["project_order"],
        )
        self.assertEqual([task["title"] for task in ordered_titles], ["Export draft", "Storyboard", "Animate intro"])
        self.assertEqual(get_task(self.conn, third["id"])["project_order"], 10)
        self.assertEqual(get_task(self.conn, first["id"])["project_order"], 20)
        self.assertEqual(get_task(self.conn, second["id"])["project_order"], 30)

    def test_project_reorder_rejects_unlinked_or_completed_tasks(self):
        project = create_project(self.conn, {"name": "Animation", "status": "active"})
        other_project = create_project(self.conn, {"name": "Website", "status": "active"})
        linked = create_task(self.conn, {"title": "Storyboard", "project_id": project["id"]})
        unlinked = create_task(self.conn, {"title": "Build page", "project_id": other_project["id"]})
        completed = update_task(
            self.conn,
            create_task(self.conn, {"title": "Done", "project_id": project["id"]})["id"],
            {"completed": True},
        )

        with self.assertRaises(ValidationError):
            reorder_project_tasks(
                self.conn,
                project["id"],
                {"task_ids": [str(linked["id"]), str(unlinked["id"])]},
            )

        with self.assertRaises(ValidationError):
            reorder_project_tasks(
                self.conn,
                project["id"],
                {"task_ids": [str(linked["id"]), str(completed["id"])]},
            )

    def test_project_completion_creates_one_achievement_and_keeps_tasks_open(self):
        project = create_project(
            self.conn,
            {
                "name": "Portfolio rebuild",
                "goal": "Ship a cleaner portfolio.",
                "status": "active",
            },
        )
        open_task = create_task(
            self.conn,
            {"title": "Publish case study", "project_id": project["id"]},
        )
        done_task = create_task(
            self.conn,
            {"title": "Draft homepage", "project_id": project["id"]},
        )
        update_task(self.conn, done_task["id"], {"completed": True})

        completed = complete_project(self.conn, project["id"])
        entries = list_entries(self.conn, {"tag": "project_completion"})
        achievements = list_achievements(
            self.conn, {"source_entry_id": str(entries[0]["id"])}
        )

        self.assertEqual(completed["status"], "complete")
        self.assertTrue(completed["completed_at"])
        self.assertEqual(len(entries), 1)
        self.assertGreaterEqual(len(achievements), 1)
        self.assertIn("Portfolio rebuild", achievements[0]["project"])
        self.assertFalse(get_task(self.conn, open_task["id"])["completed"])

        complete_project(self.conn, project["id"])
        self.assertEqual(len(list_entries(self.conn, {"tag": "project_completion"})), 1)
        self.assertEqual(
            len(list_achievements(self.conn, {"source_entry_id": str(entries[0]["id"])})),
            len(achievements),
        )

    def test_project_suggestion_can_be_accepted_as_linked_planner_task(self):
        project = create_project(
            self.conn,
            {
                "name": "Research app",
                "goal": "Plan a research tracker.",
                "status": "active",
            },
        )
        suggestion = suggest_project_next_steps(self.conn, project["id"])[0]
        task = create_task(
            self.conn,
            {
                "title": suggestion["title"],
                "notes": suggestion["guidance"],
                "project_id": project["id"],
            },
        )

        self.assertEqual(task["project_id"], str(project["id"]))
        self.assertEqual(task["project"], "Research app")
        self.assertEqual(task["project_order"], 10)
        self.assertFalse(task["completed"])

    def test_diary_entry_generates_achievement_bullets(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Auth hardening",
                "what_i_did": "Added JWT validation and cleaned redirect handling.",
                "project": "Client portal",
                "skills_used": ["Python", "JWT"],
                "outcome": "Login failures are now easier to diagnose.",
                "tags": ["security"],
            },
        )

        achievements = list_achievements(
            self.conn, {"source_entry_id": str(entry["id"])}
        )

        self.assertGreaterEqual(len(achievements), 1)
        self.assertLessEqual(len(achievements), MAX_ACHIEVEMENT_BULLETS)
        self.assertEqual(achievements[0]["source_entry_id"], entry["id"])
        self.assertEqual(achievements[0]["project"], "Client portal")
        self.assertIn("Python", achievements[0]["skills_used"])
        self.assertTrue(achievements[0]["bullet"].endswith("."))

    def test_entry_update_replaces_auto_achievements_without_duplicates(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Planner cleanup",
                "what_i_did": "Tidied the planner form.",
                "outcome": "The task flow is easier to scan.",
            },
        )
        original = list_achievements(self.conn, {"source_entry_id": str(entry["id"])})

        update_entry(
            self.conn,
            entry["id"],
            {
                "what_i_did": "Separated dashboard metrics from planner tasks.",
                "outcome": "The mobile layout now has clearer destinations.",
            },
        )
        refreshed = list_achievements(self.conn, {"source_entry_id": str(entry["id"])})

        self.assertGreaterEqual(len(refreshed), 1)
        self.assertLessEqual(len(refreshed), MAX_ACHIEVEMENT_BULLETS)
        self.assertEqual(len(refreshed), len({item["id"] for item in refreshed}))
        self.assertNotEqual(
            {item["bullet"] for item in original},
            {item["bullet"] for item in refreshed},
        )

    def test_deleting_entry_removes_linked_achievements(self):
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Journal entry",
                "what_i_did": "Logged project progress.",
            },
        )
        self.assertGreater(
            len(list_achievements(self.conn, {"source_entry_id": str(entry["id"])})),
            0,
        )

        delete_entry(self.conn, entry["id"])

        self.assertEqual(
            list_achievements(self.conn, {"source_entry_id": str(entry["id"])}),
            [],
        )

    def test_achievement_extraction_falls_back_when_openai_fails(self):
        entry = {
            "entry_date": "2026-06-03",
            "title": "Mobile planner",
            "what_i_did": "Improved the phone layout for task planning.",
            "project": "Work Diary",
            "skills_used": ["CSS"],
            "outcome": "Navigation is clearer.",
            "tags": ["mobile"],
            "difficulty": "",
            "reflection_notes": "",
        }

        with patch("app.generate_llm_achievements", side_effect=RuntimeError("boom")):
            bullets = extract_achievement_bullets(
                entry,
                {
                    "api_key": "test-key",
                    "model": "gpt-5.4-nano",
                    "reasoning_effort": "low",
                },
            )

        self.assertGreaterEqual(len(bullets), 1)
        self.assertIn("Mobile planner", bullets[0])

    def test_achievement_extraction_allows_up_to_ten_bullets(self):
        payload = {
            "output_text": "\n".join(
                f"{index}. Completed milestone {index}."
                for index in range(1, MAX_ACHIEVEMENT_BULLETS + 3)
            )
        }

        bullets = extract_openai_lines(payload)

        self.assertEqual(len(bullets), MAX_ACHIEVEMENT_BULLETS)
        self.assertEqual(bullets[-1], "Completed milestone 10.")

    def test_bootstrap_payload_combines_all_dashboard_data(self):
        task = create_task(
            self.conn,
            {
                "title": "Finish auth notes",
                "project": "Client portal",
                "priority": "high",
            },
        )
        entry = create_entry(
            self.conn,
            {
                "entry_date": "2026-06-03",
                "title": "Auth hardening",
                "what_i_did": "Added JWT validation and cleaned redirects.",
                "project": "Client portal",
                "skills_used": ["Python", "JWT"],
                "tags": ["security"],
            },
        )
        evidence = create_evidence(
            self.conn,
            {
                "work_entry_id": entry["id"],
                "title": "Merged pull request",
                "evidence_type": "github",
                "evidence_url": "https://github.com/example/repo/pull/18",
            },
        )

        payload = get_bootstrap(self.conn)

        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["id"], task["id"])
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(payload["entries"][0]["id"], entry["id"])
        self.assertEqual(payload["entries"][0]["evidence_count"], 1)
        self.assertEqual(len(payload["evidence"]), 1)
        self.assertEqual(payload["evidence"][0]["id"], evidence["id"])
        self.assertGreaterEqual(len(payload["achievements"]), 1)
        self.assertEqual(payload["achievements"][0]["source_entry_id"], entry["id"])
        self.assertIn("Client portal", payload["options"]["projects"])
        self.assertIn("Python", payload["options"]["skills"])
        self.assertIn("security", payload["options"]["tags"])

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
        self.assertFalse(path_requires_auth("/login.html"))
        self.assertFalse(path_requires_auth("/config.js"))
        self.assertFalse(path_requires_auth("/static/app.js"))

    def test_pwa_and_deployment_files_exist(self):
        root = Path(__file__).resolve().parents[1]

        self.assertTrue((root / "static" / "manifest.webmanifest").exists())
        self.assertTrue((root / "static" / "service-worker.js").exists())
        self.assertTrue((root / "static" / "favicon.svg").exists())
        self.assertTrue((root / "deploy" / "uk.co.workdiary.app.plist.example").exists())

    def test_task_start_and_end_date_controls_have_distinct_targets(self):
        html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()

        self.assertIn('data-date-target="taskDate" aria-label="Set date"', html)
        self.assertIn('data-date-target="taskStartDate" aria-label="Set start date"', html)
        self.assertIn('data-date-target="taskDueDate" aria-label="Set end date"', html)
        self.assertIn('<span id="taskDateLabel">No date</span>', html)
        self.assertIn('<span id="taskStartDateLabel">No date</span>', html)
        self.assertIn('<span id="taskEndDateLabel">No date</span>', html)

    def test_reasoning_settings_only_apply_to_reasoning_models(self):
        self.assertTrue(model_supports_reasoning("gpt-5.4-nano"))
        self.assertTrue(model_supports_reasoning("o4-mini"))
        self.assertFalse(model_supports_reasoning("gpt-4o-mini"))
        self.assertFalse(model_supports_reasoning("gpt-4.1-nano"))

    def test_task_creation_and_open_filter(self):
        task = create_task(
            self.conn,
            {
                "title": "Finish authentication notes",
                "project": "Final Project",
                "start_date": "2026-06-02",
                "start_time": "08:15",
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
        self.assertEqual(task["start_date"], "2026-06-02")
        self.assertEqual(task["start_time"], "08:15")
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

    def test_google_sync_targets_all_tasks_to_google_tasks(self):
        dated = {"title": "Timed task", "due_date": "2026-06-04", "due_time": "09:30"}
        undated = {"title": "Inbox task"}

        self.assertEqual(google_task_sync_target(dated), "google_task")
        self.assertEqual(google_task_sync_target(undated), "google_task")

        event = google_calendar_event_body({**dated, "id": 123, "notes": "Bring notes."}, "abc123")
        self.assertEqual(event["summary"], "Timed task")
        self.assertEqual(event["start"]["dateTime"], "2026-06-04T09:30:00")
        self.assertEqual(event["end"]["dateTime"], "2026-06-04T10:00:00")

        google_task = google_task_body({**undated, "id": 456, "notes": "No date yet."})
        self.assertEqual(google_task["title"], "Inbox task")
        self.assertEqual(google_task["status"], "needsAction")
        self.assertIn("Work Diary task ID: 456", google_task["notes"])

        dated_google_task = google_task_body({**dated, "id": 123})
        self.assertEqual(dated_google_task["due"], "2026-06-04T00:00:00.000Z")

    def test_google_calendar_uses_task_start_time_when_supplied(self):
        event = google_calendar_event_body(
            {
                "id": 123,
                "title": "Focused work",
                "start_date": "2026-06-04",
                "start_time": "08:15",
                "due_date": "2026-06-04",
                "due_time": "09:30",
            },
            "abc123",
        )

        self.assertEqual(event["start"]["dateTime"], "2026-06-04T08:15:00")
        self.assertEqual(event["end"]["dateTime"], "2026-06-04T09:30:00")

    def test_google_calendar_preserves_start_only_schedules(self):
        task = {
            "id": 123,
            "title": "Begin research",
            "start_date": "2026-06-04",
            "start_time": "09:00",
        }

        self.assertEqual(google_task_sync_target(task), "google_task")
        event = google_calendar_event_body(task, "abc123")
        self.assertEqual(event["start"]["dateTime"], "2026-06-04T09:00:00")
        self.assertEqual(event["end"]["dateTime"], "2026-06-04T09:30:00")

    def test_google_sync_failure_does_not_block_task_creation(self):
        with patch("app.sync_task_to_google", side_effect=RuntimeError("Google unavailable")):
            task = create_task(self.conn, {"title": "Keep local task"})

        saved = get_task(self.conn, task["id"])
        self.assertEqual(saved["title"], "Keep local task")
        self.assertIn("Google unavailable", saved["google_sync_error"])

    def test_google_sync_keeps_task_without_adding_calendar_event_when_date_is_added(self):
        previous = {
            "id": 1,
            "title": "Inbox task",
            "google_sync_target": "google_task",
            "google_task_id": "task-1",
        }
        current = {
            **previous,
            "due_date": "2026-06-04",
            "google_sync_error": "",
            "google_sync_hash": "",
        }

        with patch("app.google_is_connected", return_value=True), patch(
            "app.delete_google_task"
        ) as delete_google_task, patch("app.sync_calendar_event_for_task") as sync_calendar:
            with patch("app.get_task", return_value=current), patch(
                "app.sync_google_task_for_task"
            ) as sync_google_task:
                sync_task_to_google(self.conn, current, previous)

        delete_google_task.assert_not_called()
        sync_calendar.assert_not_called()
        sync_google_task.assert_called_once()

    def test_push_subscribe_and_unsubscribe(self):
        payload = {
            "subscription": {
                "endpoint": "https://push.example/subscription/1",
                "keys": {"p256dh": "public-key", "auth": "auth-secret"},
            },
            "user_agent": "Android Chrome",
        }

        saved = save_push_subscription(self.conn, payload)

        self.assertTrue(saved["ok"])
        subscriptions = list_push_subscriptions(self.conn)
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0]["endpoint"], payload["subscription"]["endpoint"])

        deleted = delete_push_subscription(
            self.conn, {"endpoint": payload["subscription"]["endpoint"]}
        )

        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(list_push_subscriptions(self.conn), [])

    def test_push_status_reports_config_without_private_key(self):
        save_push_subscription(
            self.conn,
            {
                "subscription": {
                    "endpoint": "https://push.example/subscription/1",
                    "keys": {"p256dh": "public-key", "auth": "auth-secret"},
                },
            },
        )

        with patch.dict(os.environ, {"VAPID_PUBLIC_KEY": "public", "VAPID_PRIVATE_KEY": "private"}):
            status = get_push_status(self.conn)

        self.assertTrue(status["publicKeyConfigured"])
        self.assertTrue(status["privateKeyConfigured"])
        self.assertEqual(status["subscriptionCount"], 1)
        self.assertNotIn("private", status.values())

    def test_task_reminder_datetime_is_ten_minutes_before_due_time(self):
        task = {
            "due_date": "2026-06-05",
            "due_time": "00:00",
        }

        reminder_at = task_reminder_datetime(task)

        self.assertEqual(reminder_at.isoformat(), "2026-06-04T23:50:00+01:00")

    def test_explicit_task_reminder_overrides_default_due_offset(self):
        reminder_at = task_reminder_datetime(
            {
                "due_date": "2026-06-05",
                "due_time": "12:00",
                "reminder_at": "2026-06-05T08:30",
            }
        )

        self.assertEqual(reminder_at.isoformat(), "2026-06-05T08:30:00+01:00")

    def test_task_lifecycle_updates_reminder_schedule_hooks(self):
        with patch("app.schedule_task_reminder") as schedule, patch(
            "app.delete_task_reminder_schedule"
        ) as delete_schedule:
            task = create_task(
                self.conn,
                {
                    "title": "Pay bill",
                    "due_date": "2026-06-05",
                    "due_time": "09:00",
                },
            )

            schedule.assert_called_once()

            update_task(self.conn, task["id"], {"completed": True})
            delete_task(self.conn, task["id"])

        self.assertGreaterEqual(delete_schedule.call_count, 2)

    def test_sync_task_reminder_schedules_backfills_future_due_tasks(self):
        create_task(
            self.conn,
            {
                "title": "Future reminder",
                "due_date": "2999-06-05",
                "due_time": "09:00",
            },
        )
        create_task(self.conn, {"title": "No time", "due_date": "2999-06-05"})

        with patch("app.schedule_task_reminder") as schedule:
            result = sync_task_reminder_schedules(self.conn)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scheduled"], 1)
        self.assertEqual(result["skipped"], 1)
        schedule.assert_called_once()

    def test_daily_summary_includes_open_today_and_overdue_tasks(self):
        due = create_task(
            self.conn,
            {
                "title": "Morning check",
                "due_date": "2026-06-05",
                "due_time": "00:00",
            },
        )
        ranged = create_task(
            self.conn,
            {
                "title": "Coursework build",
                "start_date": "2026-06-03",
                "due_date": "2026-06-07",
            },
        )
        overdue = create_task(
            self.conn,
            {
                "title": "Overdue follow-up",
                "due_date": "2026-06-04",
            },
        )
        create_task(self.conn, {"title": "Tomorrow", "due_date": "2026-06-06"})
        completed = create_task(self.conn, {"title": "Done", "due_date": "2026-06-05"})
        update_task(self.conn, completed["id"], {"completed": True})

        payload = build_daily_summary_payload(list_tasks(self.conn, {}), "2026-06-05")

        self.assertIsNotNone(payload)
        self.assertIn("3 tasks to do", payload["body"])
        self.assertIn(overdue["title"], payload["body"])
        self.assertIn(due["title"], payload["body"])
        self.assertIn(ranged["title"], payload["body"])
        self.assertNotIn("Done", payload["body"])

    def test_daily_summary_still_sends_when_no_tasks_are_active_today(self):
        create_task(self.conn, {"title": "Tomorrow", "due_date": "2026-06-06"})

        payload = build_daily_summary_payload(list_tasks(self.conn, {}), "2026-06-05")

        self.assertEqual(payload["title"], "Today's tasks")
        self.assertEqual(payload["body"], "No tasks to do today.")
        self.assertEqual(payload["tag"], "work-diary-daily-2026-06-05")

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
            create_task(self.conn, {"title": "Bad start date", "start_date": "tomorrow"})
        with self.assertRaises(ValidationError):
            create_task(
                self.conn,
                {
                    "title": "Bad range",
                    "start_date": "2026-06-10",
                    "due_date": "2026-06-09",
                },
            )
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad time", "due_time": "9am"})
        with self.assertRaisesRegex(ValidationError, "Start time must be on or before end time"):
            create_task(
                self.conn,
                {
                    "title": "Backwards time range",
                    "start_date": "2026-06-05",
                    "start_time": "14:00",
                    "due_date": "2026-06-05",
                    "due_time": "09:00",
                },
            )
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad reminder", "reminder_at": "soon"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad priority", "priority": "urgent"})
        with self.assertRaises(ValidationError):
            create_task(self.conn, {"title": "Bad repeat", "repeat_rule": "yearly"})
        with self.assertRaisesRegex(ValidationError, "JSON boolean"):
            create_task(self.conn, {"title": "Bad completion", "completed": "false"})
        with self.assertRaisesRegex(ValidationError, "240 characters"):
            create_task(self.conn, {"title": "x" * 241})

    def test_task_start_and_end_times_round_trip_through_update(self):
        task = create_task(
            self.conn,
            {
                "title": "Timed task",
                "start_date": "2026-06-05",
                "start_time": "08:15",
                "due_date": "2026-06-05",
                "due_time": "09:30",
            },
        )
        updated = update_task(
            self.conn,
            task["id"],
            {"start_time": "10:00", "due_time": "11:45"},
        )

        self.assertEqual(updated["start_time"], "10:00")
        self.assertEqual(updated["due_time"], "11:45")
        saved = get_task(self.conn, task["id"])
        self.assertEqual(saved["start_time"], "10:00")
        self.assertEqual(saved["due_time"], "11:45")

    def test_repeating_task_creates_next_task_when_completed(self):
        task = create_task(
            self.conn,
            {
                "title": "Weekly CV review",
                "start_date": "2026-06-01",
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
        self.assertEqual(open_tasks[0]["start_date"], "2026-06-08")
        self.assertEqual(open_tasks[0]["due_date"], "2026-06-10")
        self.assertEqual(open_tasks[0]["due_time"], "18:00")
        self.assertEqual(open_tasks[0]["reminder_at"], "2026-06-10T17:30")
        self.assertEqual(open_tasks[0]["repeat_rule"], "weekly")

    def test_repeating_task_supports_custom_day_interval(self):
        task = create_task(
            self.conn,
            {
                "title": "Wash hair",
                "due_date": "2026-06-04",
                "repeat_rule": "interval",
                "repeat_interval_days": 10,
            },
        )

        update_task(self.conn, task["id"], {"completed": True})
        open_tasks = list_tasks(self.conn, {"completed": "false"})

        self.assertEqual(len(open_tasks), 1)
        self.assertEqual(open_tasks[0]["title"], "Wash hair")
        self.assertEqual(open_tasks[0]["due_date"], "2026-06-14")
        self.assertEqual(open_tasks[0]["repeat_rule"], "interval")
        self.assertEqual(open_tasks[0]["repeat_interval_days"], 10)

    def test_repeating_task_stops_after_repeat_until(self):
        task = create_task(
            self.conn,
            {
                "title": "Short repeat",
                "due_date": "2026-06-04",
                "repeat_rule": "interval",
                "repeat_interval_days": 10,
                "repeat_until": "2026-06-10",
            },
        )

        update_task(self.conn, task["id"], {"completed": True})

        self.assertEqual(len(list_tasks(self.conn, {"completed": "false"})), 0)


if __name__ == "__main__":
    unittest.main()
