import json
import os

def save_llm_json(data: dict, filename: str, folder="memory"):
    """
    Save dictionary data as JSON.
    """

    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, filename)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

    print(f"✅ Saved JSON to {filepath}")

    return filepath