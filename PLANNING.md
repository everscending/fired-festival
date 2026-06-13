# Digital Doppleganger — Basic Plan

## Project Summary

A web chatbot that role-plays as a specific person and answers questions about their
career, background, and skills. It uses their LinkedIn profile (PDF) and a short bio as
its source of truth. When a visitor shares their email, or asks something the bot can't
answer, the bot emails the owner.

Built on the **OpenAI Agents SDK** (Python), structured around the four pillars of every
harness: **Chat / Loop**, **Tools**, **Guardrails**, and **Observability**.

## Tech Stack Flow Chart

```
        ┌──────────────┐
        │   Visitor    │
        └──────┬───────┘
               │  chats
               ▼
        ┌──────────────┐
        │    Gradio    │   web chat UI
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │  Python App  │   loads linkedin.pdf + summary.txt
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │   AI Agent   │   OpenAI Agents SDK + gpt-4o-mini
        └──────┬───────┘
               │  uses tools
               ▼
        ┌──────────────┐
        │   Mailtrap   │   emails owner (leads + unknown Qs)
        └──────────────┘

   Deployed on: Hugging Face Spaces
```

## The Four Pillars

Whatever framework we use, these four responsibilities are always present. Here is how
each maps onto this project and onto the OpenAI Agents SDK.

### 1. Chat / Loop — *drives reasoning across turns until the task is done*

- The OpenAI Agents SDK `Runner` drives the agent loop (model call → tool call →
  model call) until a final answer is produced.
- A single `Agent` is configured with instructions that establish the persona and
  embed the full source-of-truth documents (`linkedin.pdf` text + `summary.txt`)
  directly in the system prompt — no vector store / RAG given the small document size.
- Gradio holds the multi-turn conversation state and passes history back into the loop.

### 2. Tools — *lets the model read data and change the outside world*

- `record_user_details(email, name, notes)` — capture a lead and email the owner.
- `record_unknown_question(question)` — flag a question the bot couldn't answer and
  email the owner.
- Tools are defined with the SDK's `@function_tool` decorator.

### 3. Guardrails — *constrains inputs, outputs, and actions to safe bounds*

Kept lightweight with inline checks (no separate validation agent/model for now).

- **Input guardrail:** stay on-topic (career / background / skills); deflect or refuse
  off-topic or unsafe requests — handled primarily via system-prompt instructions.
- **Output guardrail:** never invent facts not supported by the source documents; stay
  in persona — enforced through system-prompt instructions.
- **Action guardrail:** inline validation in tool code — check email format before
  triggering the Mailtrap tool; de-duplicate / rate-limit outbound emails.

### 4. Observability — *records what happened so you can debug and improve*

- Use the OpenAI Agents SDK's built-in **tracing** to record each run (model calls, tool calls,
  guardrail trips).
- Log conversations, tool invocations, and errors for later review.
- Track basic metrics: number of leads captured, unknown questions, refusals.

## Open Questions / Next Steps

- Confirm model choice (`gpt-4o-mini` vs. another) and budget.
- Define the exact email templates Mailtrap will send.
- Confirm deployment target (Hugging Face Spaces) secrets handling for API keys.
