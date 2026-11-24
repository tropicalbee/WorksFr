# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a self-taught learner following a tutorial alone (confirmed 2026-11-23). They hit an error and can't tell whether they made a mistake or the code itself no longer works with today's libraries. Students and tutorial authors may use it too, but design decisions serve the learner first.

## Product Purpose

WorksFr takes a Python tutorial (a web page, a GitHub README, a Stack Overflow answer or a Jupyter notebook), runs every code step in a Docker sandbox against the latest library versions, and shows, step by step, what still runs (confirmed 2026-11-23). For each step that changed it explains what changed in the library. For a broken step it shows the one replacement line that ran in the sandbox, as proof of the explanation.

WorksFr does not judge the tutorial. An old tutorial can still teach well after its library moved on, so there is no verdict, grade or "worth following" rating, and WorksFr does not rewrite the tutorial or offer a fixed copy to download.

## Positioning

The sandbox decides, not the LLM. A step counts as running only after a real run, and a replacement line is shown only after it ran. Steps that can't be checked honestly (an API key, a GPU, network access, a local file) are labeled "Can't verify" with the reason, never guessed.

### Why use WorksFr instead of asking an LLM

- An LLM answers from memory and can't tell whether code works with today's library versions. WorksFr runs every step with today's libraries and shows what happened.
- An LLM can invent a fix or an API that doesn't exist and still sound sure. WorksFr shows a fix only after it has run without an error.
- Beginners don't know what to ask. They find the broken step when they reach it, often an hour in. WorksFr checks the whole tutorial before they start.
- WorksFr says when it can't tell. Steps that need an API key or a GPU are labeled "Can't verify" with the reason.
- Its fixes come with evidence. The repair uses the library's own error messages and changelogs, so the learner sees why the code changed.

Pitch line: "ChatGPT tells you what it thinks will work. WorksFr shows you what actually ran today."

## Operating Context

- Input is a pasted link or an uploaded `.ipynb`.
- A check takes minutes. Progress streams live, step by step, over server-sent events, and a reader may open or reopen the report mid-run.
- The report is read alongside the original tutorial, often in another tab.
- WorksFr runs locally on the user's own machine.

## Capabilities and Constraints

- Labels: Works, Deprecated, Broken, Can't verify. Patch verdicts: fixed, fixed but live call unverified, failed. A fixed step keeps its Broken label.
- Step counts per status (for example "6 of 8 checkable steps still run"), shown as facts, never as a grade.
- Each repair has up to three attempts. Each attempt records the patch, its explanation, the APIs that changed and the provider that proposed it.
- The report shows installed library versions and any packages that failed to install.
- Python tutorials only.
- Frontend stack: React, TypeScript (strict), Vite, Tailwind CSS and React Router, with API types generated from the backend's OpenAPI schema.

## Evidence on Hand

- Six saved real tutorials in `backend/tests/fixtures/` (pandas, scikit-learn, Keras, LangChain, OpenAI SDK through Towards Data Science and Stack Overflow).
- No users, testimonials, metrics or press exist. None may be invented.

## Product Principles

1. Status first: the reader sees at once which steps still run and which don't.
2. Explain the change in plain words, so a learner learns the library, not just the new line.
3. Show the evidence: every status and replacement line comes from a real run.
4. Describe, never judge: no grades, verdicts or ratings of the tutorial.
5. Be honest about limits: "Can't verify" with its reason beats a guess.

## Accessibility & Inclusion

No product-specific requirement established. WCAG AA contrast and keyboard access are the baseline. One exception, chosen by the user on 2026-11-23: the faintest grey (`#62666d`, about 3.3:1) used for line numbers, code comments, the step kind and waiting steps.
