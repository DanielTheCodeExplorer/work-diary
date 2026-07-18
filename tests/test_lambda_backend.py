import importlib
import json
import sys
import types
import unittest
import unittest.mock


class FakeDynamoResource:
    def Table(self, name):
        return {"name": name}


class FakeS3Client:
    def put_object(self, **kwargs):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


sys.modules.setdefault(
    "boto3",
    types.SimpleNamespace(
        resource=lambda service_name: FakeDynamoResource(),
        client=lambda service_name: FakeS3Client(),
    ),
)

lambda_backend = importlib.import_module("lambda_backend")


class LambdaBackendHelperTests(unittest.TestCase):
    def setUp(self):
        self.mcp_change_patch = unittest.mock.patch.object(lambda_backend, "mcp_record_change")
        self.mcp_record_change = self.mcp_change_patch.start()

    def tearDown(self):
        self.mcp_change_patch.stop()

    def test_mcp_initialize_and_tool_list_follow_json_rpc(self):
        initialize = lambda_backend.handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        tools = lambda_backend.handle_mcp_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )

        self.assertEqual(initialize["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", initialize["result"]["capabilities"])
        self.assertIn("search", {tool["name"] for tool in tools["result"]["tools"]})

    def test_mcp_task_view_does_not_expose_google_credentials_or_sync_ids(self):
        view = lambda_backend.mcp_task_view(
            {
                "id": "task-1",
                "title": "Private task",
                "updated_at": "2026-07-18T10:00:00Z",
                "reminder_at": "2026-07-19T09:00",
                "google_task_id": "provider-secret-id",
                "google_sync_hash": "private-hash",
                "priority": "high",
            }
        )

        self.assertNotIn("google_task_id", view)
        self.assertNotIn("google_sync_hash", view)
        self.assertNotIn("priority", view)
        self.assertEqual(view["reminder_at"], "2026-07-19T09:00")

    def test_mcp_lists_and_undoes_a_recorded_task_change(self):
        task = {
            "id": "task-undo",
            "title": "New title",
            "completed": False,
            "archived": False,
            "updated_at": "2026-07-18T14:00:00Z",
        }
        change = {
            "change_id": "change-1",
            "entity_type": "task",
            "entity_id": task["id"],
            "tool_name": "update_task_details",
            "before": {"title": "Old title", "completed": False, "archived": False},
            "after": {"title": "New title", "completed": False, "archived": False},
            "created_at": "2026-07-18T13:59:00Z",
        }
        with unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=task
        ), unittest.mock.patch.object(
            lambda_backend, "mcp_list_changes", return_value=[change]
        ):
            listed = lambda_backend.call_mcp_tool(
                "list_task_changes", {"task_id": task["id"], "limit": 10}
            )
        self.assertEqual(listed["structuredContent"]["changes"][0]["change_id"], "change-1")

        restored = {**task, "title": "Old title", "updated_at": "2026-07-18T14:01:00Z"}
        arguments = {
            "task_id": task["id"],
            "change_id": "change-1",
            "expected_updated_at": task["updated_at"],
            "idempotency_key": "undo-task-change-1",
        }
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=task
        ), unittest.mock.patch.object(
            lambda_backend, "mcp_get_change", return_value=change
        ), unittest.mock.patch.object(
            lambda_backend, "update_task", return_value=restored
        ) as update_task:
            result = lambda_backend.call_mcp_tool("undo_task_change", arguments)

        update_task.assert_called_once_with(task["id"], change["before"], create_repeat=False)
        self.assertEqual(result["structuredContent"]["task"]["title"], "Old title")
        self.assertEqual(self.mcp_record_change.call_args.kwargs["extra"], {"undo_of": "change-1"})

    def test_mcp_undoes_a_recorded_project_task_order(self):
        project = {
            "id": "project-undo",
            "name": "Launch",
            "status": "active",
            "updated_at": "2026-07-18T15:00:00Z",
        }
        change = {
            "change_id": "project-change-1",
            "before": {"name": "Launch", "status": "active"},
            "after": {"name": "Launch", "status": "active"},
            "before_task_ids": ["task-a", "task-b"],
            "after_task_ids": ["task-b", "task-a"],
        }
        restored_project = {**project, "updated_at": "2026-07-18T15:01:00Z"}
        restored_tasks = [
            {"id": "task-a", "title": "A", "project_order": 10},
            {"id": "task-b", "title": "B", "project_order": 20},
        ]
        arguments = {
            "project_id": project["id"],
            "change_id": change["change_id"],
            "expected_updated_at": project["updated_at"],
            "idempotency_key": "undo-project-order-1",
        }
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_project", return_value=project
        ), unittest.mock.patch.object(
            lambda_backend, "mcp_get_change", return_value=change
        ), unittest.mock.patch.object(
            lambda_backend,
            "reorder_project_tasks",
            return_value={"project": restored_project, "tasks": restored_tasks},
        ) as reorder:
            result = lambda_backend.call_mcp_tool("undo_project_change", arguments)

        reorder.assert_called_once_with(project["id"], {"task_ids": ["task-a", "task-b"]})
        self.assertEqual([task["id"] for task in result["structuredContent"]["tasks"]], ["task-a", "task-b"])
        self.assertEqual(
            self.mcp_record_change.call_args.kwargs["extra"],
            {
                "before_task_ids": ["task-b", "task-a"],
                "after_task_ids": ["task-a", "task-b"],
                "undo_of": "project-change-1",
            },
        )

    def test_mcp_history_snapshots_exclude_provider_and_account_fields(self):
        snapshot = lambda_backend.mcp_snapshot(
            {
                "title": "Safe",
                "notes": "Planning only",
                "google_task_id": "provider-id",
                "access_token": "secret",
            },
            lambda_backend.MCP_TASK_SNAPSHOT_FIELDS,
        )

        self.assertEqual(snapshot["title"], "Safe")
        self.assertNotIn("google_task_id", snapshot)
        self.assertNotIn("access_token", snapshot)

    def test_mcp_archive_accepts_open_task_and_preserves_revision_check(self):
        open_task = {
            "id": "task-1",
            "title": "Still open",
            "completed": False,
            "archived": False,
            "updated_at": "2026-07-18T10:00:00Z",
        }
        arguments = {
            "task_id": "task-1",
            "expected_updated_at": open_task["updated_at"],
            "idempotency_key": "archive-task-1",
        }

        archived_task = {**open_task, "archived": True, "archived_at": "2026-07-18T10:05:00Z"}
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=open_task
        ), unittest.mock.patch.object(
            lambda_backend, "update_task", return_value=archived_task
        ) as update_task:
            result = lambda_backend.call_mcp_tool("archive_task", arguments)

        update_task.assert_called_once_with("task-1", {"archived": True})
        self.assertTrue(result["structuredContent"]["task"]["archived"])
        self.assertFalse(result["structuredContent"]["task"]["completed"])

    def test_mcp_restore_reverses_archive_without_changing_completion(self):
        archived_task = {
            "id": "task-2",
            "title": "Changed my mind",
            "completed": False,
            "archived": True,
            "archived_at": "2026-07-18T10:05:00Z",
            "updated_at": "2026-07-18T10:05:00Z",
        }
        restored_task = {**archived_task, "archived": False, "archived_at": ""}
        arguments = {
            "task_id": "task-2",
            "expected_updated_at": archived_task["updated_at"],
            "idempotency_key": "restore-task-2",
        }

        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=archived_task
        ), unittest.mock.patch.object(
            lambda_backend, "update_task", return_value=restored_task
        ) as update_task:
            result = lambda_backend.call_mcp_tool("restore_task", arguments)

        update_task.assert_called_once_with("task-2", {"archived": False})
        self.assertFalse(result["structuredContent"]["task"]["archived"])
        self.assertFalse(result["structuredContent"]["task"]["completed"])

    def test_mcp_reopen_and_detail_updates_are_revision_checked(self):
        completed_task = {
            "id": "task-3",
            "title": "Old title",
            "completed": True,
            "archived": False,
            "updated_at": "2026-07-18T11:00:00Z",
        }
        reopened_task = {**completed_task, "completed": False, "completed_at": ""}
        common = {
            "task_id": "task-3",
            "expected_updated_at": completed_task["updated_at"],
            "idempotency_key": "reopen-task-3",
        }

        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=completed_task
        ), unittest.mock.patch.object(
            lambda_backend, "update_task", return_value=reopened_task
        ) as update_task:
            result = lambda_backend.call_mcp_tool("reopen_task", common)

        update_task.assert_called_once_with("task-3", {"completed": False})
        self.assertFalse(result["structuredContent"]["task"]["completed"])

        edited_task = {**reopened_task, "title": "New title", "project": "", "project_id": ""}
        detail_args = {
            **common,
            "title": "New title",
            "project": "",
            "idempotency_key": "edit-task-3",
        }
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_task", return_value=completed_task
        ), unittest.mock.patch.object(
            lambda_backend, "update_task", return_value=edited_task
        ) as update_task:
            lambda_backend.call_mcp_tool("update_task_details", detail_args)

        update_task.assert_called_once_with(
            "task-3", {"title": "New title", "project": "", "project_id": ""}
        )

    def test_mcp_can_set_reminders_and_recurrence(self):
        task = {
            "id": "task-4",
            "title": "Plan week",
            "completed": False,
            "archived": False,
            "updated_at": "2026-07-18T12:00:00Z",
        }
        base = {
            "task_id": "task-4",
            "expected_updated_at": task["updated_at"],
        }
        cases = [
            (
                "set_task_reminder",
                {**base, "reminder_at": "2026-07-20T08:30", "idempotency_key": "reminder-task-4"},
                {"reminder_at": "2026-07-20T08:30"},
            ),
            (
                "set_task_recurrence",
                {
                    **base,
                    "repeat_rule": "interval",
                    "repeat_interval_days": 3,
                    "repeat_until": "2026-08-31",
                    "idempotency_key": "repeat-task-4",
                },
                {"repeat_rule": "interval", "repeat_interval_days": 3, "repeat_until": "2026-08-31"},
            ),
        ]

        for tool_name, arguments, expected_update in cases:
            with self.subTest(tool=tool_name), unittest.mock.patch.object(
                lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
            ), unittest.mock.patch.object(
                lambda_backend, "get_task", return_value=task
            ), unittest.mock.patch.object(
                lambda_backend, "update_task", return_value={**task, **expected_update}
            ) as update_task:
                lambda_backend.call_mcp_tool(tool_name, arguments)
                update_task.assert_called_once_with("task-4", expected_update)

    def test_mcp_rejects_task_and_project_changes_after_revision_drift(self):
        task = {
            "id": "task-stale",
            "title": "Changed elsewhere",
            "completed": False,
            "archived": False,
            "updated_at": "2026-07-18T13:00:00Z",
        }
        task_arguments = {
            "task_id": task["id"],
            "expected_updated_at": "2026-07-18T12:59:00Z",
            "idempotency_key": "stale-task-change",
        }
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(lambda_backend, "get_task", return_value=task):
            with self.assertRaisesRegex(lambda_backend.ValidationError, "changed since it was read"):
                lambda_backend.call_mcp_tool("reopen_task", task_arguments)

        project = {
            "id": "project-stale",
            "name": "Changed project",
            "status": "active",
            "updated_at": "2026-07-18T13:00:00Z",
        }
        project_arguments = {
            "project_id": project["id"],
            "status": "paused",
            "expected_updated_at": "2026-07-18T12:59:00Z",
            "idempotency_key": "stale-project-change",
        }
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(lambda_backend, "get_project", return_value=project):
            with self.assertRaisesRegex(lambda_backend.ValidationError, "changed since it was read"):
                lambda_backend.call_mcp_tool("set_project_status", project_arguments)

    def test_mcp_project_tools_cover_non_delete_planning_actions(self):
        project = {
            "id": "project-1",
            "name": "Launch",
            "goal": "Ship",
            "deadline": "2026-08-01",
            "status": "active",
            "color": "#5DD4C0",
            "notes": "",
            "completed_at": "",
            "created_at": "2026-07-18T09:00:00Z",
            "updated_at": "2026-07-18T12:00:00Z",
        }
        with unittest.mock.patch.object(lambda_backend, "list_projects", return_value=[project]):
            listed = lambda_backend.call_mcp_tool("list_projects", {"status": "active"})
        self.assertEqual(listed["structuredContent"]["projects"][0]["id"], "project-1")

        arguments = {
            "project_id": "project-1",
            "status": "complete",
            "expected_updated_at": project["updated_at"],
            "idempotency_key": "complete-project-1",
        }
        completed = {**project, "status": "complete", "completed_at": "2026-07-18T12:30:00Z"}
        with unittest.mock.patch.object(
            lambda_backend, "mcp_idempotent", side_effect=lambda _name, _key, operation: operation()
        ), unittest.mock.patch.object(
            lambda_backend, "get_project", return_value=project
        ), unittest.mock.patch.object(
            lambda_backend, "complete_project", return_value=completed
        ) as complete_project:
            result = lambda_backend.call_mcp_tool("set_project_status", arguments)

        complete_project.assert_called_once_with("project-1")
        self.assertEqual(result["structuredContent"]["project"]["status"], "complete")

    def test_mcp_rejects_an_unauthorized_request_with_resource_metadata(self):
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/mcp",
            "headers": {"host": "api.example.com", "x-forwarded-proto": "https"},
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        }
        with unittest.mock.patch.object(
            lambda_backend, "get_config_value", side_effect=lambda key, default="": "mcp-secret" if key == "MCP_SIGNING_SECRET" else default
        ):
            result = lambda_backend.lambda_handler(
                event, types.SimpleNamespace(aws_request_id="mcp-request")
            )

        self.assertEqual(result["statusCode"], 401)
        self.assertIn("resource_metadata=", result["headers"]["WWW-Authenticate"])

    def test_google_status_requires_both_destinations_to_be_ready(self):
        integration = {
            "access_token": "access",
            "refresh_token": "refresh",
            "calendar_id": "calendar-1",
            "tasklist_id": "",
            "scope": " ".join(lambda_backend.GOOGLE_SCOPES),
        }
        with unittest.mock.patch.object(
            lambda_backend, "get_google_integration", return_value=integration
        ), unittest.mock.patch.object(
            lambda_backend,
            "read_google_config",
            return_value={
                "client_id": "client",
                "client_secret": "secret",
                "redirect_uri": "https://example.com/callback",
                "frontend_url": "https://example.com",
            },
        ), unittest.mock.patch.object(
            lambda_backend, "google_failed_task_count", return_value=0
        ):
            status = lambda_backend.google_status()

        self.assertTrue(status["connected"])
        self.assertFalse(status["ready"])

    def test_google_status_accepts_legacy_calendar_scope_but_requires_default_tasklist(self):
        integration = {
            "refresh_token": "refresh",
            "calendar_id": "legacy-calendar",
            "tasklist_id": "tasks-1",
            "scope": "https://www.googleapis.com/auth/calendar.app.created https://www.googleapis.com/auth/tasks",
        }
        with unittest.mock.patch.object(
            lambda_backend, "get_google_integration", return_value=integration
        ), unittest.mock.patch.object(
            lambda_backend,
            "read_google_config",
            return_value={
                "client_id": "client",
                "client_secret": "secret",
                "redirect_uri": "https://example.com/callback",
                "frontend_url": "https://example.com",
            },
        ), unittest.mock.patch.object(
            lambda_backend, "google_failed_task_count", return_value=0
        ):
            status = lambda_backend.google_status()

        self.assertTrue(status["connected"])
        self.assertFalse(status["needs_reauthorization"])
        self.assertFalse(status["ready"])

    def test_ensure_google_calendar_selects_primary_without_creating_a_calendar(self):
        with unittest.mock.patch.object(
            lambda_backend, "save_google_integration"
        ) as save_integration, unittest.mock.patch.object(
            lambda_backend, "google_api"
        ) as google_api:
            calendar_id = lambda_backend.ensure_google_calendar(
                {"calendar_id": "legacy-calendar"}
            )

        self.assertEqual(calendar_id, "primary")
        save_integration.assert_called_once_with(
            {"calendar_id": "primary", "last_error": ""}
        )
        google_api.assert_not_called()

    def test_ensure_google_tasklist_selects_default_without_creating_a_list(self):
        with unittest.mock.patch.object(
            lambda_backend, "google_api"
        ) as google_api, unittest.mock.patch.object(
            lambda_backend, "save_google_integration"
        ) as save_integration:
            tasklist_id = lambda_backend.ensure_google_tasklist({"tasklist_id": ""})

        self.assertEqual(tasklist_id, "@default")
        google_api.assert_not_called()
        save_integration.assert_called_once_with(
            {"tasklist_id": "@default", "last_error": ""}
        )

    def test_retry_google_connection_finishes_destinations_before_sync(self):
        with unittest.mock.patch.object(
            lambda_backend, "ensure_google_destinations"
        ) as ensure_destinations, unittest.mock.patch.object(
            lambda_backend,
            "retry_google_sync",
            return_value={"ok": True, "synced": 0, "failed": 0, "skipped": 0},
        ) as retry_sync, unittest.mock.patch.object(
            lambda_backend, "save_google_integration"
        ) as save_integration, unittest.mock.patch.object(
            lambda_backend, "google_status", return_value={"ready": True}
        ):
            result = lambda_backend.retry_google_connection()

        ensure_destinations.assert_called_once_with()
        retry_sync.assert_called_once_with(include_all=True)
        save_integration.assert_called_once_with({"last_error": ""})
        self.assertTrue(result["status"]["ready"])

    def test_response_preserves_empty_list_body(self):
        response = lambda_backend.response(200, [])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"]), [])

    def test_lambda_fallback_achievement_bullets_are_concise(self):
        bullets = lambda_backend.fallback_achievement_bullets(
            {
                "title": "Mobile planner",
                "what_i_did": "Improved the phone layout for task planning.",
                "outcome": "Navigation is clearer.",
                "skills_used": ["CSS"],
            }
        )

        self.assertGreaterEqual(len(bullets), 1)
        self.assertLessEqual(len(bullets), lambda_backend.MAX_ACHIEVEMENT_BULLETS)
        self.assertTrue(bullets[0].endswith("."))

    def test_lambda_openai_line_extraction_accepts_numbered_output(self):
        bullets = lambda_backend.extract_openai_lines(
            {
                "output_text": "1. Improved mobile task planning.\n2. Clarified dashboard progress."
            }
        )

        self.assertEqual(
            bullets,
            ["Improved mobile task planning.", "Clarified dashboard progress."],
        )

    def test_lambda_openai_line_extraction_caps_at_ten(self):
        bullets = lambda_backend.extract_openai_lines(
            {
                "output_text": "\n".join(
                    f"{index}. Completed milestone {index}."
                    for index in range(1, lambda_backend.MAX_ACHIEVEMENT_BULLETS + 3)
                )
            }
        )

        self.assertEqual(len(bullets), lambda_backend.MAX_ACHIEVEMENT_BULLETS)
        self.assertEqual(bullets[-1], "Completed milestone 10.")

    def test_lambda_project_suggestion_fallback_is_capped(self):
        suggestions = lambda_backend.fallback_project_suggestions(
            {
                "name": "Portfolio launch",
                "goal": "Publish a cleaner portfolio.",
                "deadline": "2026-07-01",
            },
            {"open_tasks": [{"title": "Draft case study"}]},
        )

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertLessEqual(len(suggestions), lambda_backend.MAX_PROJECT_SUGGESTIONS)
        self.assertIn("title", suggestions[0])
        self.assertIn("guidance", suggestions[0])

    def test_lambda_project_suggestion_json_extraction_caps_at_three(self):
        suggestions = lambda_backend.extract_project_suggestions_from_payload(
            {
                "output_text": json.dumps(
                    [
                        {"title": f"Task {index}", "guidance": "Do the next useful thing."}
                        for index in range(1, 6)
                    ]
                )
            }
        )

        self.assertEqual(len(suggestions), lambda_backend.MAX_PROJECT_SUGGESTIONS)
        self.assertEqual(suggestions[0]["title"], "Task 1")

    def test_lambda_task_and_entry_payloads_preserve_project_id(self):
        task = lambda_backend.row_to_task(
            {
                "id": "task-1",
                "title": "Plan",
                "project_id": "project-1",
                "project": "Portfolio",
                "start_time": "08:15",
                "project_order": 20,
            }
        )
        entry = lambda_backend.row_to_entry(
            {"id": "entry-1", "title": "Note", "project_id": "project-1", "project": "Portfolio"}
        )

        self.assertEqual(task["project_id"], "project-1")
        self.assertEqual(task["start_time"], "08:15")
        self.assertEqual(task["project_order"], 20)
        self.assertEqual(entry["project_id"], "project-1")

    def test_lambda_project_payload_includes_completed_at(self):
        project = lambda_backend.row_to_project(
            {
                "id": "project-1",
                "name": "Portfolio",
                "status": "complete",
                "completed_at": "2026-06-09T10:00:00+00:00",
            }
        )

        self.assertEqual(project["completed_at"], "2026-06-09T10:00:00+00:00")

    def test_lambda_task_reminder_datetime_is_ten_minutes_before_due_time(self):
        reminder_at = lambda_backend.task_reminder_datetime(
            {"due_date": "2026-06-05", "due_time": "00:00"}
        )

        self.assertEqual(reminder_at.isoformat(), "2026-06-04T23:50:00+01:00")

    def test_lambda_explicit_reminder_overrides_default_due_offset(self):
        reminder_at = lambda_backend.task_reminder_datetime(
            {
                "due_date": "2026-06-05",
                "due_time": "12:00",
                "reminder_at": "2026-06-05T08:30",
            }
        )

        self.assertEqual(reminder_at.isoformat(), "2026-06-05T08:30:00+01:00")

    def test_lambda_rejects_backwards_same_day_time_range(self):
        with self.assertRaisesRegex(
            lambda_backend.ValidationError,
            "Start time must be on or before end time",
        ):
            lambda_backend.validate_task_time_range(
                {"start_time": "14:00", "due_time": "09:00"},
                "2026-06-05",
                "2026-06-05",
            )

    def test_lambda_requires_a_real_json_boolean_for_completion(self):
        with self.assertRaisesRegex(lambda_backend.ValidationError, "JSON boolean"):
            lambda_backend.validate_boolean("false", "Completed")

    def test_lambda_daily_summary_includes_open_today_and_overdue_tasks(self):
        payload = lambda_backend.build_daily_summary_payload(
            [
                {"title": "Open today", "due_date": "2026-06-05", "completed": False},
                {"title": "Overdue follow-up", "due_date": "2026-06-04", "completed": False},
                {
                    "title": "Range task",
                    "start_date": "2026-06-03",
                    "due_date": "2026-06-07",
                    "completed": False,
                },
                {"title": "Done today", "due_date": "2026-06-05", "completed": True},
                {"title": "Tomorrow", "due_date": "2026-06-06", "completed": False},
            ],
            "2026-06-05",
        )

        self.assertIn("3 tasks to do", payload["body"])
        self.assertIn("Open today", payload["body"])
        self.assertIn("Overdue follow-up", payload["body"])
        self.assertIn("Range task", payload["body"])
        self.assertNotIn("Done today", payload["body"])

    def test_lambda_daily_summary_still_sends_when_no_tasks_are_active_today(self):
        payload = lambda_backend.build_daily_summary_payload(
            [{"title": "Tomorrow", "due_date": "2026-06-06", "completed": False}],
            "2026-06-05",
        )

        self.assertEqual(payload["title"], "Today's tasks")
        self.assertEqual(payload["body"], "No tasks to do today.")
        self.assertEqual(payload["tag"], "work-diary-daily-2026-06-05")

    def test_lambda_test_push_uses_neutral_payload(self):
        payload = lambda_backend.build_test_push_payload()

        self.assertEqual(payload["title"], "Work Diary test")
        self.assertEqual(payload["body"], "Test reminder is working.")
        self.assertNotIn("Tomiwa", payload["body"])

    def test_lambda_web_push_is_retained_for_twenty_four_hours(self):
        subscription = {
            "subscription": {
                "endpoint": "https://push.example/subscription/current",
                "keys": {"p256dh": "key", "auth": "auth"},
            }
        }

        with unittest.mock.patch.object(
            lambda_backend,
            "read_vapid_config",
            return_value={
                "public_key": "public",
                "private_key": "private",
                "subject": "mailto:test@example.com",
            },
        ), unittest.mock.patch.object(lambda_backend, "webpush") as webpush:
            lambda_backend.send_web_push(subscription, {"title": "Scheduled reminder"})

        self.assertEqual(
            webpush.call_args.kwargs["ttl"],
            lambda_backend.WEB_PUSH_TTL_SECONDS,
        )
        self.assertEqual(lambda_backend.WEB_PUSH_TTL_SECONDS, 86400)

    def test_lambda_schedules_task_reminder_with_exact_offset(self):
        calls = []

        class FakeScheduler:
            def update_schedule(self, **kwargs):
                calls.append(kwargs)

        with unittest.mock.patch.object(
            lambda_backend, "SCHEDULER_CLIENT", FakeScheduler()
        ), unittest.mock.patch.object(
            lambda_backend,
            "WORK_DIARY_FUNCTION_ARN",
            "arn:aws:lambda:eu-west-2:123:function:WorkDiaryAPI",
        ), unittest.mock.patch.object(
            lambda_backend,
            "REMINDER_SCHEDULER_ROLE_ARN",
            "arn:aws:iam::123:role/scheduler",
        ):
            lambda_backend.schedule_task_reminder(
                {
                    "id": "abc",
                    "title": "Midnight task",
                    "due_date": "2999-06-05",
                    "due_time": "00:00",
                    "completed": False,
                }
            )

        self.assertEqual(calls[0]["Name"], "task-abc-due10")
        self.assertEqual(calls[0]["ScheduleExpression"], "at(2999-06-04T23:50:00)")
        self.assertEqual(calls[0]["ScheduleExpressionTimezone"], "Europe/London")
        target_input = json.loads(calls[0]["Target"]["Input"])
        self.assertEqual(target_input["expected_reminder_at"], "2999-06-04T23:50:00+01:00")

    def test_lambda_skips_a_stale_task_reminder_event(self):
        task = {
            "id": "abc",
            "title": "Updated task",
            "due_date": "2026-07-18",
            "due_time": "12:00",
            "completed": False,
        }
        with unittest.mock.patch.object(lambda_backend, "get_task", return_value=task), \
             unittest.mock.patch.object(lambda_backend, "send_push_payload") as send_push:
            result = lambda_backend.handle_reminder_event(
                {
                    "action": "task_reminder",
                    "task_id": "abc",
                    "expected_reminder_at": "2026-07-18T08:00:00+01:00",
                }
            )

        self.assertEqual(result["skipped"], "Stale task reminder.")
        send_push.assert_not_called()

    def test_lambda_skips_an_expired_task_reminder_event(self):
        task = {
            "id": "abc",
            "title": "Old task",
            "due_date": "2026-07-18",
            "due_time": "12:00",
            "completed": False,
        }
        expected = lambda_backend.task_reminder_datetime(task).isoformat()
        with unittest.mock.patch.object(lambda_backend, "get_task", return_value=task), \
             unittest.mock.patch.object(
                 lambda_backend, "task_reminder_event_is_timely", return_value=False
             ), unittest.mock.patch.object(lambda_backend, "send_push_payload") as send_push:
            result = lambda_backend.handle_reminder_event(
                {
                    "action": "task_reminder",
                    "task_id": "abc",
                    "expected_reminder_at": expected,
                }
            )

        self.assertEqual(result["skipped"], "Expired task reminder.")
        send_push.assert_not_called()

    def test_lambda_skips_an_already_delivered_task_reminder(self):
        task = {
            "id": "abc",
            "title": "Current task",
            "due_date": "2026-07-18",
            "due_time": "12:00",
            "completed": False,
        }
        expected = lambda_backend.task_reminder_datetime(task).isoformat()
        task["reminder_sent_for"] = expected
        with unittest.mock.patch.object(lambda_backend, "get_task", return_value=task), \
             unittest.mock.patch.object(
                 lambda_backend, "task_reminder_event_is_timely", return_value=True
             ), unittest.mock.patch.object(lambda_backend, "send_push_payload") as send_push:
            result = lambda_backend.handle_reminder_event(
                {
                    "action": "task_reminder",
                    "task_id": "abc",
                    "expected_reminder_at": expected,
                }
            )

        self.assertEqual(result["skipped"], "Task reminder already sent.")
        send_push.assert_not_called()

    def test_lambda_records_a_successful_task_reminder_delivery(self):
        task = {
            "id": "abc",
            "title": "Current task",
            "due_date": "2026-07-18",
            "due_time": "12:00",
            "completed": False,
        }
        expected = lambda_backend.task_reminder_datetime(task).isoformat()
        with unittest.mock.patch.object(lambda_backend, "get_task", return_value=task), \
             unittest.mock.patch.object(
                 lambda_backend, "task_reminder_event_is_timely", return_value=True
             ), unittest.mock.patch.object(
                 lambda_backend,
                 "send_push_payload",
                 return_value={"ok": True, "sent": 1, "failed": 0, "expired": 0},
             ), unittest.mock.patch.object(
                 lambda_backend, "record_task_reminder_delivery"
             ) as record_delivery:
            result = lambda_backend.handle_reminder_event(
                {
                    "action": "task_reminder",
                    "task_id": "abc",
                    "expected_reminder_at": expected,
                }
            )

        self.assertEqual(result["sent"], 1)
        record_delivery.assert_called_once_with("abc", expected)

    def test_task_reminder_timeliness_has_a_bounded_delivery_window(self):
        timezone = lambda_backend.ZoneInfo(lambda_backend.REMINDER_TIMEZONE)
        reminder_at = lambda_backend.dt.datetime(2026, 7, 18, 12, 0, tzinfo=timezone)

        self.assertTrue(
            lambda_backend.task_reminder_event_is_timely(
                reminder_at,
                reminder_at + lambda_backend.dt.timedelta(minutes=30),
            )
        )
        self.assertFalse(
            lambda_backend.task_reminder_event_is_timely(
                reminder_at,
                reminder_at + lambda_backend.dt.timedelta(minutes=31),
            )
        )

    def test_lambda_syncs_existing_future_due_task_reminders(self):
        tasks = [
            {
                "id": "future",
                "title": "Future reminder",
                "due_date": "2999-06-05",
                "due_time": "09:00",
                "completed": False,
            },
            {
                "id": "missing-time",
                "title": "No time",
                "due_date": "2999-06-05",
                "due_time": "",
                "completed": False,
            },
        ]

        with unittest.mock.patch.object(
            lambda_backend, "schedule_task_reminder"
        ) as schedule, unittest.mock.patch.object(
            lambda_backend, "delete_task_reminder_schedule"
        ) as delete_schedule:
            result = lambda_backend.sync_task_reminder_schedules(tasks)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scheduled"], 1)
        self.assertEqual(result["skipped"], 1)
        schedule.assert_called_once_with(tasks[0])
        delete_schedule.assert_called_once_with("missing-time")

    def test_lambda_handler_rejects_source_less_direct_actions(self):
        result = lambda_backend.lambda_handler(
            {"action": "daily_summary"},
            types.SimpleNamespace(aws_request_id="request-1"),
        )

        self.assertEqual(result["statusCode"], 401)
        self.assertEqual(json.loads(result["body"])["error"], "Login required.")

    def test_lambda_handler_does_not_expose_unexpected_key_errors(self):
        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/api/health",
            "headers": {},
        }
        with unittest.mock.patch.object(
            lambda_backend, "route_api", side_effect=KeyError("internal_secret_field")
        ):
            result = lambda_backend.lambda_handler(
                event,
                types.SimpleNamespace(aws_request_id="request-2"),
            )

        payload = json.loads(result["body"])
        self.assertEqual(result["statusCode"], 500)
        self.assertEqual(payload["error"], "Unexpected server error.")
        self.assertEqual(payload["request_id"], "request-2")
        self.assertNotIn("internal_secret_field", result["body"])

    def test_lambda_handler_sanitizes_provider_errors(self):
        event = {
            "requestContext": {"http": {"method": "GET"}},
            "rawPath": "/api/health",
            "headers": {},
        }
        with unittest.mock.patch.object(
            lambda_backend,
            "route_api",
            side_effect=lambda_backend.GoogleIntegrationError("provider token: secret-value"),
        ):
            result = lambda_backend.lambda_handler(
                event,
                types.SimpleNamespace(aws_request_id="request-3"),
            )

        self.assertEqual(result["statusCode"], 502)
        self.assertEqual(
            json.loads(result["body"])["error"],
            "Google integration request failed. Please reconnect or try again.",
        )
        self.assertNotIn("secret-value", result["body"])

    def test_lambda_handler_rejects_malformed_events_cleanly(self):
        result = lambda_backend.lambda_handler(
            [],
            types.SimpleNamespace(aws_request_id="request-4"),
        )

        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(
            json.loads(result["body"])["error"],
            "Lambda event must be an object.",
        )

    def test_expired_push_subscriptions_are_removed(self):
        class ExpiredPush(Exception):
            response = types.SimpleNamespace(status_code=410)

        subscription = {
            "endpoint": "https://push.example/subscription/old",
            "subscription": {"endpoint": "https://push.example/subscription/old", "keys": {}},
        }

        with unittest.mock.patch.object(
            lambda_backend, "WebPushException", ExpiredPush
        ), unittest.mock.patch.object(
            lambda_backend, "list_push_subscriptions", return_value=[subscription]
        ), unittest.mock.patch.object(
            lambda_backend, "send_web_push", side_effect=ExpiredPush()
        ), unittest.mock.patch.object(
            lambda_backend, "delete_push_subscription_by_endpoint"
        ) as delete_subscription:
            result = lambda_backend.send_push_payload({"title": "Test"})

        self.assertEqual(result["expired"], 1)
        delete_subscription.assert_called_once_with(subscription["endpoint"])

    def test_lambda_push_status_reports_config_without_private_key(self):
        with unittest.mock.patch.object(
            lambda_backend,
            "read_vapid_config",
            return_value={"public_key": "public", "private_key": "private", "subject": "mailto:test@example.com"},
        ), unittest.mock.patch.object(
            lambda_backend,
            "list_push_subscriptions",
            return_value=[{"id": "sub"}],
        ), unittest.mock.patch.object(lambda_backend, "webpush", object()):
            status = lambda_backend.get_push_status()

        self.assertTrue(status["configured"])
        self.assertEqual(status["subscriptionCount"], 1)
        self.assertNotIn("private", status.values())


if __name__ == "__main__":
    unittest.main()
