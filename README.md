# WorksFr

WorksFr checks whether a Python coding tutorial still works with today's library versions. When a step is broken, it finds out what changed in the library, patches the code, and proves the fix by running it.

Status: early development. The setup steps below describe the planned layout.

## How it works

1. You paste a tutorial link (blog post, Medium article, Stack Overflow answer, GitHub README) or upload a Jupyter notebook.
2. WorksFr pulls out the code blocks in order, works out which libraries they import, and estimates when the tutorial was written.
3. It runs the blocks one at a time in a Docker sandbox that has the latest version of each library installed.
4. Each step gets a label (see below).
5. For a broken step, the repair loop collects evidence about what changed, asks an LLM for a patch, and runs the patched code. It stops after three tries.
6. You get a freshness score, a short note for each fix that says what changed in the library, and a fixed notebook or `.py` file to download.

The LLM never decides that a step works. Only a successful run in the sandbox does.

## Step labels

| Label | Meaning |
| --- | --- |
| Works | Ran with no error and no deprecation warning. |
| Deprecated | Ran, but the library raised a `DeprecationWarning` or `FutureWarning`. |
| Broken | Raised an error. If the repair loop finds a patch that runs, the step also shows "fixed" and how many tries it took. |
| Can't verify | Needs something the sandbox doesn't have, such as a real API key, a GPU, a large download, or keyboard input. |

The freshness score counts the steps that still run as written (Works plus Deprecated) out of the steps that could be checked. A result looks like "7/10 steps still run, 2 couldn't be checked".

## Tech stack

| Part | Tools |
| --- | --- |
| Frontend | React, TypeScript, Vite, Tailwind CSS |
| Backend | FastAPI (Python) |
| Extraction | trafilatura and BeautifulSoup for web pages, nbformat for notebooks |
| Sandbox | Docker |
| LLM | Any OpenAI-compatible API. NVIDIA, Gemini and Groq are set up as providers. |
| Doc lookup | PyPI JSON API, GitHub releases, Tavily search |
| Storage | SQLite |

## Requirements

- Docker Desktop, running
- Python 3.12 or newer
- Node.js 20 or newer
- An API key for at least one LLM provider: NVIDIA (build.nvidia.com), Google Gemini, or Groq
- A Tavily API key (optional; without it, doc lookup uses only PyPI and GitHub)

## Setup

Build the sandbox image:

```powershell
docker build -t worksfr-sandbox sandbox/
```

Start the backend:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Fill in your API keys in `backend/.env` before you run a check.

Start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

### Use WorksFr from an agent (MCP)

WorksFr ships an MCP server, so Claude Code, Claude Desktop, Cursor or any other MCP client can check a tutorial and read back the verified fixes. The server only talks to the backend's HTTP API. The sandbox stays in the backend, and the MCP process never runs tutorial code. Start the backend first (`uvicorn app.main:app`). If it isn't on `http://127.0.0.1:8000`, set `WORKSFR_API_URL`.

Add it to Claude Code. Run this from the project root, and use your own path:

```powershell
claude mcp add worksfr -- C:\path\to\WorksFr\backend\.venv\Scripts\python.exe -m app.mcp_server
```

The server has to start with `backend/` as its working directory so that `app` imports. If your client doesn't let you set one, use `claude mcp add worksfr --env PYTHONPATH=C:\path\to\WorksFr\backend -- ...` or the `cwd` field below.

For Claude Desktop and Cursor, add this to the client's MCP config:

```json
{
  "mcpServers": {
    "worksfr": {
      "command": "C:\\path\\to\\WorksFr\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\path\\to\\WorksFr\\backend"
    }
  }
}
```

Tools: `check_tutorial`, `check_code`, `get_report` and `recent_checks`. Try asking: "Check whether https://www.geeksforgeeks.org/python-pandas-dataframe-append/ still works." A check takes minutes.

## Project layout

```
backend/    FastAPI app: extraction, sandbox control, repair loop, API
frontend/   React + TypeScript web app
sandbox/    Dockerfile and the in-container runner
docs/       Architecture, build plan, progress log, issues log
```

## Limitations

- WorksFr runs code from the internet. The sandbox limits memory, CPU, processes and network, but Docker is not a perfect security boundary. Run WorksFr on your own machine and don't expose it publicly.
- Messy pages sometimes extract badly, for example when output and code share one block or when `>>>` prompts are mixed in.
- Steps that need a real API key, a GPU or a large download are marked "can't verify". For SDKs like OpenAI's, WorksFr runs the code with a fake key. If the code gets as far as an authentication error, the imports and call signatures still work, and the step says so.
- Only Python tutorials are supported.
- The tutorial's age is an estimate based on page metadata and the code style.
- The repair loop patches one step at a time. Fixes that need changes across several steps may fail.

## Related work

- UpgrAIder (GitHub Next, archived) updated deprecated library calls in code using release notes.
- [Context7](https://github.com/upstash/context7) serves version-specific library docs to LLMs.
- [ABP TutorialValidator](https://abp.io/community/articles/automatically-validate-your-documentation-how-we-built-a-tutorial-validator-m3ozgkhv) runs the ABP framework's own tutorials to catch broken steps.
- Linters and coding agents flag or upgrade deprecated calls in your own project.

WorksFr works on someone else's tutorial, before you write any code. It takes any tutorial link, runs it, and repairs the broken steps.
