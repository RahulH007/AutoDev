import json
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

def get_qa_doc_prompt(qa_json: dict) -> list:
    json_str = json.dumps(qa_json, indent=2)

    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content="""
You are a Lead QA Engineer writing a formal Quality Assurance Report.

IMPORTANT — PURPOSE OF THIS DOCUMENT

This document will be converted into a polished **PDF for the client**.
It provides transparency into the testing process, highlighting what was found and the overall code health.

------------------------------------------------
STYLE GUIDELINES
------------------------------------------------

• Keep it professional and clear.
• Use tables for listing bugs and test cases to ensure scannability.
• Focus on the impact of issues rather than deep technical jargon.

------------------------------------------------
FORMATTING RULES
------------------------------------------------

• Output must be **valid Markdown**.
• Use the exact headings defined below.
"""),
        HumanMessagePromptTemplate.from_template("""
QA OUTPUT DATA
{json_str}

------------------------------------------------

Using the provided data, write a **QA Report Document** following this structure:

# Quality Assurance Report

## 1. Executive Summary
Provide the overall assessment and state whether the build PASSED or FAILED based on the criteria.

## 2. Quality Metrics
List:
- Total Bugs Found
- Critical Issues
- Total Tests Written

## 3. Service Reports
For each service, provide:
- The Service Name and Quality Score.
- A table of bugs found (Severity, Description, Suggested Fix).
- A table of test cases generated (Test Name, Target File, Description).

## 4. Recommendations
List the actionable recommendations provided in the QA data.

------------------------------------------------

Write the QA Report now.
""")
    ])

    return prompt_template.format_messages(
        json_str=json_str
    )
