"""Curated few-shot examples for both structured (JSON) and free-text prompt variants (plan §5.2).
"""

from __future__ import annotations

# Few-shot examples in JSON format (V4 Combined)
FEW_SHOT_JSON_EXAMPLES = """
Example 1: Fully Grounded Question
User Context:
---
[Source: Deliverwinning value] (https://stratpoint.com/mobileappdev/)
Bring products and experiences to where your customers choose to be — anywhere!
Stratpoint designs, architects, develops, secures, and future-proofs mobile apps, so you can constantly deliver products, experiences, and value to your customers.
What we do includes UI/UX Design and Development.
---
Question: Does Stratpoint do mobile app development?

Assistant Grounded JSON Answer:
{
  "reasoning": "The user is asking if Stratpoint does mobile app development. The context for the page 'Deliverwinning value' with URL 'https://stratpoint.com/mobileappdev/' explicitly states that 'Stratpoint designs, architects, develops, secures, and future-proofs mobile apps.' It also mentions UI/UX design and development. Therefore, this is fully answerable from the context.",
  "answer": "Yes, we offer comprehensive mobile app development services. We design, architect, develop, secure, and future-proof mobile applications, which includes both UI/UX design and core development.",
  "citations": [
    {
      "url": "https://stratpoint.com/mobileappdev/",
      "title": "Deliverwinning value"
    }
  ],
  "is_grounded": true,
  "confidence": 1.0
}

Example 2: Partially Grounded Question
User Context:
---
[Source: SELL SMART,SELL MORE] (https://stratpoint.com/retail/)
Stratpoint delivers end-to-end solutions to help your brand stay engaged with your customers.
Our Works:
SM Malls Online: The SM Malls Online project includes features that help manage the inventory of all store items for mall administrators, tenants, and the digital commerce team.
Aeroworx E-Commerce: AWS Cloud Migration and Managed Services. Aeroworx needed to migrate to AWS Cloud.
---
Question: What retail-related projects has Stratpoint done, and how much did they cost?

Assistant Grounded JSON Answer:
{
  "reasoning": "The user asks two things: 1) retail-related projects Stratpoint has done, and 2) how much they cost. Looking at the context, Stratpoint has worked on 'SM Malls Online' (a project to manage store item inventory for administrators, tenants, and the commerce team) and 'Aeroworx E-Commerce' (AWS Cloud migration). However, there is no pricing or cost information mentioned anywhere in the context. Therefore, I can answer the projects portion but must refuse/declare lack of info for the cost portion.",
  "answer": "We have worked on retail-related projects including SM Malls Online, which helps manage store item inventory for mall administrators, tenants, and digital commerce teams, and Aeroworx E-Commerce, where we performed AWS Cloud migration. However, there is no information in the provided context regarding the cost of these projects.",
  "citations": [
    {
      "url": "https://stratpoint.com/retail/",
      "title": "SELL SMART,SELL MORE"
    }
  ],
  "is_grounded": true,
  "confidence": 0.8
}

Example 3: Out-of-Scope / Unanswerable Question
User Context:
---
[Source: Deliverwinning value] (https://stratpoint.com/mobileappdev/)
Stratpoint designs, architects, develops, secures, and future-proofs mobile apps.
---
Question: What is Stratpoint's current stock market ticker and share price?

Assistant Grounded JSON Answer:
{
  "reasoning": "The user is asking for Stratpoint's stock market ticker and share price. The provided context only covers mobile app development services. There is no information regarding public trading, stock tickers, or share pricing in the context. This is out of scope and cannot be answered.",
  "answer": "I am sorry, but the provided context does not contain information regarding our stock market ticker or share price. For corporate financial details, please refer to our official site or contact us directly.",
  "citations": [],
  "is_grounded": false,
  "confidence": 0.0
}
"""

# Few-shot examples in free-text format (V1 Few-Shot)
FEW_SHOT_TEXT_EXAMPLES = """
Example 1:
User Context:
---
[Source: Deliverwinning value] (https://stratpoint.com/mobileappdev/)
Stratpoint designs, architects, develops, secures, and future-proofs mobile apps, so you can constantly deliver products, experiences, and value to your customers. UI/UX Design and Development.
---
Question: Does Stratpoint do mobile app development?

Assistant:
Yes, we offer mobile app development services. We design, architect, develop, secure, and future-proof mobile apps, covering both UI/UX design and core development.

Sources used:
- Deliverwinning value (https://stratpoint.com/mobileappdev/)

Example 2:
User Context:
---
[Source: SELL SMART,SELL MORE] (https://stratpoint.com/retail/)
SM Malls Online: The SM Malls Online project includes features that help manage the inventory of all store items.
Aeroworx E-Commerce: AWS Cloud Migration and Managed Services.
---
Question: What retail-related projects has Stratpoint done, and how much did they cost?

Assistant:
We have worked on retail-related projects including SM Malls Online (which manages store inventory) and Aeroworx E-Commerce (AWS Cloud migration). However, there is no information in the provided context regarding the cost of these projects.

Sources used:
- SELL SMART,SELL MORE (https://stratpoint.com/retail/)

Example 3:
User Context:
---
[Source: Deliverwinning value] (https://stratpoint.com/mobileappdev/)
Stratpoint designs, architects, develops, secures, and future-proofs mobile apps.
---
Question: What is Stratpoint's current stock market ticker and share price?

Assistant:
I am sorry, but the provided context does not contain information regarding our stock market ticker or share price. For corporate financial details, please refer to our official site or contact us directly.
"""
