import unittest
from pathlib import Path


class ButtonLoadingTests(unittest.TestCase):
    def test_shared_button_guard_prevents_repeated_clicks(self):
        source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")

        self.assertIn("const [loading,setLoading]=useState(false)", text)
        self.assertIn("if(disabled||loading)return", text)
        self.assertIn("disabled={disabled||loading}", text)
        self.assertIn("aria-busy={loading||undefined}", text)
        self.assertIn("className=\"animate-spin\"", text)
        self.assertIn("finally{setLoading(false)}", text)


if __name__ == "__main__":
    unittest.main()
