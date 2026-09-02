from pathlib import Path
import runpy


runpy.run_path(str(Path(__file__).with_name("build_fund_page_enrichment_pack.py")), run_name="__main__")
