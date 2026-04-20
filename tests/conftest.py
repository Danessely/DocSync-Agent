from __future__ import annotations

import os


# Keep pytest hermetic even if the shell environment has LangSmith enabled.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGSMITH_API_KEY", None)
os.environ.pop("LANGSMITH_ENDPOINT", None)
os.environ.pop("LANGCHAIN_TRACING_V2", None)
