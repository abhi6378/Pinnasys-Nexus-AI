import unittest
from pathlib import Path


class ApiContractSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = (Path(__file__).resolve().parent.parent / "api" / "routes.py").read_text()

    def test_chat_request_uses_typed_connector_context(self):
        self.assertIn("class ConnectorContextRequest(BaseModel):", self.source)
        self.assertIn("connector_context: Optional[ConnectorContextRequest]", self.source)
        self.assertIn("normalize_connector_context(payload)", self.source)

    def test_routes_use_dynamic_auth_required_helper(self):
        self.assertIn("is_auth_required()", self.source)
        self.assertNotIn("if AUTH_REQUIRED", self.source)


if __name__ == "__main__":
    unittest.main()
