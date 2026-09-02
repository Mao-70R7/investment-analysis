from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "生产程序" / "southern_dpapi_credentials.py"
SPEC = importlib.util.spec_from_file_location("southern_dpapi_credentials_test", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SouthernDpapiCredentialsTest(unittest.TestCase):
    def test_credentials_are_never_written_as_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "southern.dpapi"
            with patch.object(MODULE, "protect_bytes", side_effect=lambda value: b"encrypted:" + value[::-1]), patch.object(
                MODULE, "unprotect_bytes", side_effect=lambda value: value.removeprefix(b"encrypted:")[::-1]
            ):
                MODULE.write_credentials(path, "account-example", "secret-example")
                raw = path.read_text(encoding="ascii")
                self.assertNotIn("account-example", raw)
                self.assertNotIn("secret-example", raw)
                self.assertEqual(MODULE.read_credentials(path), ("account-example", "secret-example"))


if __name__ == "__main__":
    unittest.main()
