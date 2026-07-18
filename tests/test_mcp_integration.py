import json
import unittest

from mcp_integration import (
    MCP_PROTOCOL_VERSION,
    oauth_server_metadata,
    pkce_s256,
    protected_resource_metadata,
    sign_claims,
    tool_descriptors,
    verify_claims,
)


class McpIntegrationTests(unittest.TestCase):
    def test_signed_claims_are_type_audience_and_expiry_bound(self):
        token = sign_claims(
            {"typ": "mcp_access", "aud": "https://example.com/mcp", "exp": 200},
            "separate-secret",
        )

        self.assertIsNotNone(
            verify_claims(
                token,
                "separate-secret",
                token_type="mcp_access",
                audience="https://example.com/mcp",
                now=100,
            )
        )
        self.assertIsNone(
            verify_claims(token, "wrong", token_type="mcp_access", now=100)
        )
        self.assertIsNone(
            verify_claims(token, "separate-secret", token_type="mcp_access", now=200)
        )

    def test_pkce_uses_s256(self):
        self.assertEqual(
            pkce_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_metadata_points_to_the_private_mcp_resource(self):
        origin = "https://api.example.com"

        self.assertEqual(
            protected_resource_metadata(origin)["resource"],
            "https://api.example.com/mcp",
        )
        self.assertEqual(
            oauth_server_metadata(origin)["registration_endpoint"],
            "https://api.example.com/oauth/register",
        )

    def test_tool_contract_includes_standard_reads_and_confirmed_writes(self):
        tools = {tool["name"]: tool for tool in tool_descriptors()}

        self.assertEqual(MCP_PROTOCOL_VERSION, "2025-06-18")
        self.assertIn("search", tools)
        self.assertIn("fetch", tools)
        self.assertTrue(tools["search"]["annotations"]["readOnlyHint"])
        self.assertIn("expected_updated_at", tools["complete_task"]["inputSchema"]["required"])
        self.assertTrue(tools["reschedule_task"]["annotations"]["idempotentHint"])

        search_schema = tools["search"]["inputSchema"]
        self.assertEqual(search_schema["required"], ["query"])
        self.assertFalse(search_schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
