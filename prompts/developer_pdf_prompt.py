import json
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

def get_developer_doc_prompt(user_requirements: str, developer_json: dict) -> list:
    json_str = json.dumps(developer_json, indent=2)

    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content="""
You are a Lead Developer explaining what was built to a client.

IMPORTANT — PURPOSE OF THIS DOCUMENT

This document will be converted into a polished **PDF for the client**.
It serves as a Development Summary, showing the client that their requirements have been translated into actual code.

------------------------------------------------
STYLE GUIDELINES
------------------------------------------------

• Keep it high-level, clear, and professional.
• Focus on the "what" and "how to run it".
• Do NOT include large blocks of code.
• Use bullet points for file structures.

------------------------------------------------
FORMATTING RULES
------------------------------------------------

• Output must be **valid Markdown**.
• Use the exact headings defined below.
"""),
        HumanMessagePromptTemplate.from_template("""
USER REQUIREMENTS
{user_requirements}

DEVELOPER OUTPUT DATA
{json_str}

------------------------------------------------

Using the provided data, write a **Development Summary Document** following this structure:

# Development Summary

## 1. Project Overview
A brief paragraph summarizing what was built based on the user requirements.

## 2. Implemented Services
For each service defined in the developer output, list its name and provide a 1-sentence description of what it does, followed by a bulleted list of the main files created for it. 
(Do not include the actual code).

## 3. Local Setup Instructions
Provide clear, step-by-step instructions on how to set up the project locally (using the setup_instructions and dependency_files from the data).

## 4. Development Notes
Summarize any important implementation details or limitations the client should be aware of.

------------------------------------------------

Write the Development Summary now.
""")
    ])

    return prompt_template.format_messages(
        user_requirements=user_requirements,
        json_str=json_str
    )
