import unittest
from pathlib import Path


class ButtonLoadingTests(unittest.TestCase):
    def test_shared_button_loading_state_and_guard(self):
        source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")

        self.assertIn("const [internalLoading,setInternalLoading]=useState(false)", text)
        self.assertIn("loading=false", text)
        self.assertIn("const activeLoading=loading||internalLoading", text)
        self.assertIn("if(disabled||activeLoading)return", text)
        self.assertIn("disabled={disabled||activeLoading}", text)
        self.assertIn("aria-busy={activeLoading||undefined}", text)
        self.assertIn('className="animate-spin"', text)
        self.assertIn("const remaining=Math.max(0,250-(Date.now()-started))", text)
        self.assertIn("setTimeout(()=>setInternalLoading(false),remaining)", text)
        self.assertIn("loading={sending}", text)
        self.assertIn("loading={busy}", text)
        self.assertIn("t('Loading...')", text)


if __name__ == "__main__":
    unittest.main()
