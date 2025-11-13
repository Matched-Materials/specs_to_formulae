# src/ingest/run_text_extractor.py
from __future__ import annotations
import asyncio
import json
import logging
import queue
import threading
from typing import Any, Dict, Optional
import os, sys, argparse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from .spec_sheet_extractor_agent.agent import spec_sheet_extractor_agent

logger = logging.getLogger(__name__)

def _drain_runner(
    runner: Runner,
    user_id: str,
    session_id: str,
    user_message: Content,
    q: queue.Queue,
) -> None:
    """Runs the agent and puts the last text response into the queue."""
    try:
        final_text: Optional[str] = None
        for ev in runner.run(user_id=user_id, session_id=session_id, new_message=user_message):
            if hasattr(ev, "text") and isinstance(ev.text, str) and ev.text.strip():
                final_text = ev.text.strip()
        q.put(("ok", final_text))
    except Exception as e:
        q.put(("err", f"Agent runner failed: {e}"))

def run_text_extraction(pdf_path: str, timeout_s: int = 180) -> str:
    """
    Extracts text from a PDF and runs the spec_sheet_extractor_agent on it.
    """
    if pdfplumber is None:
        raise ImportError("pdfplumber is not installed. Please run 'pip install pdfplumber'.")

    # 1. Extract text from the PDF
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"

    if not full_text.strip():
        return "Error: No text could be extracted from the PDF."

    # 2. Set up and run the ADK agent
    app_name = "text_extractor"
    user_id = "test_user"
    session_id = f"text-extract-{os.urandom(8).hex()}"
    user_message = Content(role="user", parts=[Part(text=full_text)])

    session_service = InMemorySessionService()
    asyncio.run(session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id))
    runner = Runner(agent=spec_sheet_extractor_agent, app_name=app_name, session_service=session_service)

    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=_drain_runner, args=(runner, user_id, session_id, user_message, q), daemon=False)
    t.start()
    status, info = q.get(timeout=timeout_s)
    t.join(timeout=1)

    if status != "ok":
        raise RuntimeError(f"Agent execution failed: {info}")

    return info or "Agent returned no response."

if __name__ == "__main__":
    # Set up basic logging for the test run
    # --- Add .env loading when run as a script ---
    from dotenv import load_dotenv
    from pathlib import Path
    project_root = Path(__file__).parent.parent.parent
    load_dotenv(dotenv_path=project_root / ".env", override=True)
    # -----------------------------------------

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Run the text extractor agent on a single PDF.")
    parser.add_argument("pdf_path", help="The path to the PDF file to process.")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"Error: File not found at {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"--- Running Spec Sheet Extractor Agent on: {args.pdf_path} ---")
        result = run_text_extraction(args.pdf_path)
        print("\n>>> Agent Response: <<<")
        print(result)
        print("--------------------------\n")
    except Exception as e:
        print(f"\nAn error occurred during the agent run: {e}", file=sys.stderr)
        sys.exit(1)