"""
brain/memory_extractor.py  —  Extracts useful facts from LLM outputs
                               and saves them back into Brain AI
"""
import json
from sqlalchemy.orm import Session
from llm.client import generate_json
from storage import repositories as repo


EXTRACTION_PROMPT = """
You are a business knowledge extractor.

Given the following conversation or output, extract any useful business facts
that should be remembered for future tasks.

Output a JSON object with this exact shape:
{{
  "has_facts": true or false,
  "facts": [
    {{"title": "short label", "content": "the fact", "tags": ["tag1", "tag2"]}}
  ],
  "profile_updates": {{
    "company_name": "",
    "tone": "",
    "audience": "",
    "services": "",
    "pricing": "",
    "goals": ""
  }}
}}

Only include profile_updates fields that are explicitly mentioned.
Leave them as empty strings if not mentioned.

Content to analyze:
{content}
"""


def extract_and_save(workspace_id: str, content: str, db: Session):
    """
    Runs extraction on any text and saves useful facts to Brain AI.
    Called automatically after every helper execution.
    """
    try:
        prompt = EXTRACTION_PROMPT.format(content=content[:2000])
        raw = generate_json(prompt)
        data = json.loads(raw)

        # Save extracted facts to knowledge base
        if data.get("has_facts"):
            for fact in data.get("facts", []):
                if fact.get("content"):
                    repo.add_knowledge(
                        db, workspace_id,
                        type_="text",
                        title=fact.get("title", "Extracted fact"),
                        content=fact["content"],
                        tags=fact.get("tags", ["auto-extracted"])
                    )

        # Update brain profile if new fields found
        profile_updates = {
            k: v for k, v in data.get("profile_updates", {}).items() if v
        }
        if profile_updates:
            repo.update_brain(db, workspace_id, profile_updates)

    except Exception:
        # Extraction is best-effort — never crash the main flow
        pass
