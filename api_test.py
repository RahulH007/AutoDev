import os
from dotenv import load_dotenv
from langchain_cerebras import ChatCerebras

def test_cerebras_connection():
    # 1. Load the .env file
    print("--- 1. Checking Environment ---")
    # Note: load_dotenv() returns True if it finds a file, but we check the key specifically
    load_dotenv()
    
    # Cerebras uses CEREBRAS_API_KEY by default
    api_key = os.getenv("CEREBRAS_API_KEY")
    
    if not api_key:
        print("❌ Error: CEREBRAS_API_KEY is missing from your .env file.")
        print("Get one at: https://cloud.cerebras.ai")
        return

    print(f"✅ Found API Key starting with: {api_key[:7]}...")

    # 2. Initialize the LLM
    print("\n--- 2. Initializing LLM ---")
    try:
        # Cerebras model name for 70B is typically "llama-3.3-70b"
        llm = ChatCerebras(
            model="qwen-3-235b-a22b-instruct-2507",
            temperature=0,
            cerebras_api_key=api_key
        )
        print("✅ Cerebras Client initialized successfully.")
    except Exception as e:
        print(f"❌ Initialization Failed: {e}")
        return

    # 3. Make a test call
    print("\n--- 3. Testing Model Response ---")
    try:
        # Cerebras is incredibly fast, expect an almost instant reply
        response = llm.invoke("Hello Cerebras! Reply with 'Connection Successful' if you can read this.")
        print(f"🤖 Model Response: {response.content}")
        print("\n🎉 Your Cerebras API key and environment are working perfectly!")
    except Exception as e:
        print(f"❌ API Call Failed: {e}")
        print("\nPossible issues:")
        print("- Your CEREBRAS_API_KEY might be invalid.")
        print("- You may have exceeded your daily token limit.")
        print("- Ensure you have 'langchain-cerebras' installed (pip install langchain-cerebras).")

if __name__ == "__main__":
    test_cerebras_connection()