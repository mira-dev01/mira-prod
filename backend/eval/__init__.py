"""Everything the eval suite needs that is not a scenario: the seeded host (`seed.py`) and the
judge LLM (`judge_llm.py`).

This runs in the harness process too (it imports the judge factory), where nothing else has
set the environment: backend/.env says ENVIRONMENT=production, which makes `app.config` refuse
to load without production secrets. An eval is never production.
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")
