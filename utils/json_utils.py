import json
import re
import os


def save_llm_json(response_text: str, filename: str, folder: str):
    """
    Extract JSON from LLM response and save it to a folder.
    """

    # remove ```json formatting
    cleaned = re.sub(r"```json|```", "", response_text).strip()

    data = json.loads(cleaned)

    # create folder if it doesn't exist
    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, filename)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

    print(f"✅ Saved JSON to {filepath}")

    return cleaned