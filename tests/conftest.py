import os
import sys
import tempfile
from pathlib import Path

# Testene skal aldri røre den ekte users.db: pek DATABASE_URL til en
# midlertidig fil før backend importeres.
_tmp_db = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db}"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # main.py leser frontend/ med relative stier

TEMPLATE_1076 = ROOT / "data" / "input" / "1076-OSDM-template.json"
