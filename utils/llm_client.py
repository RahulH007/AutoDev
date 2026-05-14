from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
import os 

load_dotenv()

llm = ChatGoogleGenerativeAI(
        model="gemini-3-flash-preview",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.2,
        # Gemini handles retries well internally
        max_retries=3 
    )
def get_structured_llm(schema):
    """
    Return an LLM configured to output a Pydantic schema.
    """
    return llm.with_structured_output(schema)

def llm_call(prompt):
    response = llm.invoke(prompt)
    content = response.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content
