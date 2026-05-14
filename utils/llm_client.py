from dotenv import load_dotenv
from langchain_cerebras import ChatCerebras
import os 

load_dotenv()

llm = ChatCerebras(
    model="qwen-3-235b-a22b-instruct-2507", 
    temperature=0.2,
    timeout=60 
)
def get_structured_llm(schema):
    """
    Return an LLM configured to output a Pydantic schema.
    """
    return llm.with_structured_output(schema)

def llm_call(prompt):
    response = llm.invoke(prompt)
    return response.content
