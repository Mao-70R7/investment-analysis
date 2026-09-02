from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "enumerate_qieman_catalog.py"
SPEC = importlib.util.spec_from_file_location("enumerate_qieman_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EnumerateQiemanCatalogTests(unittest.TestCase):
    def test_parse_bounds_rejects_zero_area(self) -> None:
        self.assertEqual(MODULE.parse_bounds("[10,20][30,40]"), (10, 20, 30, 40))
        self.assertIsNone(MODULE.parse_bounds("[0,0][0,0]"))

    def test_parse_catalog_and_strategy_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            xml = Path(temp_dir) / "window.xml"
            xml.write_text(
                """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <node text="严选策略" resource-id="title" bounds="[0,0][10,10]" selected="false" />
  <node text="短期稳健" resource-id="pkg:id/tvTab" bounds="[10,20][30,40]" selected="true" />
  <node text="长期投资" resource-id="pkg:id/tvTab" bounds="[40,20][60,40]" selected="false" />
  <node text="示例策略" resource-id="pkg:id/tvName" bounds="[10,50][100,100]" selected="false" />
  <node text="" resource-id="/alfa/portfolio/ZH124523/index?stamp=1" bounds="[0,0][100,100]" selected="false" />
</hierarchy>""",
                encoding="utf-8",
            )
            catalog = MODULE.parse_catalog_xml(xml)
            self.assertTrue(catalog["is_catalog"])
            self.assertEqual(catalog["selected_tab"], "短期稳健")
            self.assertEqual(catalog["products"], [{"name": "示例策略", "bounds": (10, 50, 100, 100)}])
            self.assertEqual(MODULE.parse_strategy_codes(xml), ["ZH124523"])


if __name__ == "__main__":
    unittest.main()
