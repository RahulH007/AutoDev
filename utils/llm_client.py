from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.2
)
def get_structured_llm(schema):
    """
    Return an LLM configured to output a Pydantic schema.
    """
    return llm.with_structured_output(schema)

def llm_call(prompt):
    response = llm.invoke(prompt)
    return response.content
