import importlib
import json
import sys
import types
import unittest


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
        self.assertLessEqual(len(bullets), 3)
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


if __name__ == "__main__":
    unittest.main()
