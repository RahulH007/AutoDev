from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

import json
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate


def get_pm_doc_prompt(user_requirements: str, json_response: dict) -> list:
    json_str = json.dumps(json_response, indent=2)

    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content="""You are a senior product manager at a top-tier technology company with 15+ years of experience shipping successful B2B and B2C products.

IMPORTANT — PURPOSE OF THIS DOCUMENT:
Your PRD will be converted into a polished PDF delivered directly to the client. The client must walk away clearly understanding where their project is heading — what's being built, why, and what success looks like.

CRITICAL — KEEP IT CONCISE:
- This is a CLIENT-FACING document, not an internal engineering spec.
- Clients are NOT technical — they care about outcomes, not implementation details.
- Every section must be brief, clear, and scannable.
- If a section can be said in 3 bullet points, do not write 10.
- Avoid technical jargon, deep implementation details, and exhaustive lists.
- When in doubt, cut it out. Clarity beats completeness.
- The full document must be 900–1200 words maximum.
- Limit MVP features to 4–6 maximum.

Hallucination Guard:
- Do not invent features, users, or requirements not present or implied by the structured analysis.
- If the brief and structured analysis conflict, prefer the structured analysis.

Formatting Rules:
- Output must be valid Markdown.
- Use headings exactly as specified in the template.
- Do not add sections outside the defined structure.
- Short paragraphs and bullet points over dense prose.
- Bold key terms on first use.
- Use tables only when they genuinely aid clarity.

Tone & Style:
- Plain, confident business English.
- Think: executive briefing, not engineering documentation."""),

        HumanMessagePromptTemplate.from_template("""<product_brief>
{user_requirements}
</product_brief>

<structured_analysis>
{json_str}
</structured_analysis>

---

Write a concise, client-friendly PRD using the structure below. Every section should be short and to the point. No placeholders, no TBDs, no unnecessary detail.

---

# [Product Name] — Product Requirements Document

## 1. Executive Summary
2–3 sentences max. What is it, who is it for, and why does it matter?

## 2. Problem Covering
3–5 bullet points covering: the problem, who faces it, and the impact of not solving it.

## 3. Goals & Success Metrics
- 2–3 business goals
- 3 measurable KPIs with targets

## 4. Target Users
One short paragraph per persona. Name, role, and key pain point. Max 2 personas.

## 5. Scope
### In Scope
Bullet list — what this version covers.
### Out of Scope
Bullet list — what is explicitly excluded.

## 6. Core Features (MVP)
4–6 features maximum. For each:
- **Feature Name** — one line description
- What it does and why it matters (2–3 sentences max)
- 2–3 acceptance criteria

Post-MVP: a single bullet list, no elaboration.

## 7. Key User Flows
Short numbered steps per flow (happy path only). Max 5 flows.

## 8. Non-Functional Requirements
| Category | Requirement | Target |
|----------|------------|--------|
Cover only: Performance, Security, Scalability.

## 9. Open Questions & Risks
Top 3–5 items only:
| # | Question / Risk | Status |
|---|----------------|--------|

---

Now write the complete PRD. Keep it tight — a client should be able to read and understand this in under 10 minutes.""")
    ])

    return prompt_template.format_messages(
        user_requirements=user_requirements,
        json_str=json_str
    )