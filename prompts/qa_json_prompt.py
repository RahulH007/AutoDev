from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

def get_qa_prompt(prd_json: dict, architect_json: dict, code_manifest: dict, actual_code_content: str) -> list:
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content="""
You are a Senior QA Engineer working in an AI-powered autonomous software development system.

Your responsibility is to review the generated source code and ensure it meets the requirements defined in the PRD and Architecture.

----------------------------------------------------
QA RESPONSIBILITIES
----------------------------------------------------

1. Review the generated code files.
2. Identify any bugs, security vulnerabilities, missing error handling, or logical errors.
3. Generate unit test code to verify the functionality (DO NOT attempt to execute them, just write the code).
4. Assign a severity to each bug: CRITICAL, MAJOR, or MINOR.
5. Score the code quality for each service from 1 to 10.
6. Provide actionable recommendations.

----------------------------------------------------
ROUTING LOGIC
----------------------------------------------------
If you find ANY critical issues, OR if any service scores less than 7/10, the system will route the code back to the Developer Agent for a retry.
Be objective and strict. If the code is broken, fail it.

----------------------------------------------------
OUTPUT FORMAT
----------------------------------------------------
Your output MUST be valid JSON matching the QA Schema.
Do not output markdown blocks surrounding the JSON.
"""),
        HumanMessagePromptTemplate.from_template("""
PRD:
{prd_json}

ARCHITECTURE:
{architect_json}

CODE MANIFEST:
{code_manifest}

ACTUAL GENERATED CODE CONTENT:
{actual_code_content}

----------------------------------------------------
Analyze the generated code against the PRD and Architecture. 
Produce a comprehensive QA Report.
""")
    ])

    return prompt_template.format_messages(
        prd_json=prd_json,
        architect_json=architect_json,
        code_manifest=code_manifest,
        actual_code_content=actual_code_content
    )
