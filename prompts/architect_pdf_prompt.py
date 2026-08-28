import json

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate


def get_architecture_doc_prompt(user_requirements: str, prd_json: dict):

    json_str = json.dumps(prd_json, indent=2)

    prompt_template = ChatPromptTemplate.from_messages([

        SystemMessage(content="""
You are a senior Software Architect with extensive experience explaining complex systems to non-technical stakeholders.

IMPORTANT — PURPOSE OF THIS DOCUMENT

The architecture document will be converted into a polished **PDF for the client**.

The client should clearly understand:

• how the system will be structured  
• what major components exist  
• how different parts interact  
• how the system will scale and remain reliable  

This document must be **clear, concise, and understandable by non-technical readers**.

------------------------------------------------
STYLE GUIDELINES
------------------------------------------------

• Use plain, simple language
• Avoid deep technical jargon
• Prefer bullet points over long paragraphs
• Focus on system understanding, not code details
• Think like explaining architecture in a product review meeting

------------------------------------------------
CONTENT GUIDELINES
------------------------------------------------

Explain the architecture at a **conceptual level**.

Do NOT include:

• code snippets
• detailed algorithms
• framework configuration

Instead explain:

• system components
• responsibilities of each component
• how data flows through the system
• how the system will scale
• how reliability is ensured

------------------------------------------------
DOCUMENT LENGTH
------------------------------------------------

The document should be **800–1200 words maximum**.

------------------------------------------------
FORMATTING RULES
------------------------------------------------

• Output must be **valid Markdown**
• Use headings exactly as defined
• Keep sections concise
• Use bullet lists where appropriate
• Use tables only when helpful
"""),

        HumanMessagePromptTemplate.from_template("""
USER REQUIREMENTS
<user_requirements>
{user_requirements}
</user_requirements>

PRODUCT REQUIREMENTS DOCUMENT
<prd_json>
{json_str}
</prd_json>

------------------------------------------------

Using the PRD above, generate a **client-friendly Architecture Document** using the structure below.

------------------------------------------------

# System Architecture Overview

## 1. Architecture Summary
Explain the high-level architecture of the system in 2–3 short paragraphs.

What type of system architecture is used and why it is suitable for this product.

## 2. Key System Components
Describe the major components of the system.

For each component explain:
- its role
- what responsibility it has
- how it interacts with other parts of the system

## 3. Data Flow
Explain how data moves through the system.

Use a numbered flow describing the typical user journey through the system.

## 4. Database Design
Explain what kind of data will be stored and how it supports the system.

Include:
- major data entities
- purpose of the database

## 5. External Integrations
List any external APIs or services used and explain their purpose.

## 6. Scalability & Performance
Explain how the system can handle increasing usage.

Focus on:
- scalability
- caching
- system performance

## 7. Security Considerations
Explain the high-level approach to security including:

- authentication
- data protection
- API security

## 8. Deployment Overview
Explain how the system will be deployed.

The system should run locally using:

• Docker  
• Docker Compose  

Explain the components involved in deployment.

## 9. Future Improvements
List potential improvements that could be added in later versions.

------------------------------------------------

Write the complete Architecture Document now.
""")
    ])

    return prompt_template.format_messages(
        user_requirements=user_requirements,
        json_str=json_str
    )
