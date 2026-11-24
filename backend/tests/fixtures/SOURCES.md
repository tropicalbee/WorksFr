# Test fixtures

Each fixture is the raw bytes of a real page, saved with curl on 2026-11-23. Each has a hand-labelled `<name>.expected.json` listing the code blocks in page order. Extraction tests compare against those files.

| Name | File | Source URL | Fetched | Library | What we expect to be broken |
| --- | --- | --- | --- | --- | --- |
| pandas_append | pandas_append.html | https://www.geeksforgeeks.org/python-pandas-dataframe-append/ | 2026-11-23 | pandas | All four examples call `DataFrame.append`, which pandas 2.0 removed. The page itself notes the removal. |
| sklearn_boston | sklearn_boston.html | https://datascienceplus.com/linear-regression-from-scratch-in-python/ | 2026-11-23 | scikit-learn | The second block calls `load_boston`, removed in scikit-learn 1.2. Later blocks fail because `boston`, `X` and `y` are never defined. |
| keras_notebook | keras_notebook.ipynb | https://raw.githubusercontent.com/rajshah4/image_keras/master/notebook.ipynb | 2026-11-23 | Keras | `fit_generator`, `evaluate_generator` and `predict_generator` are gone from current Keras. `Convolution2D` and `keras.preprocessing.image.ImageDataGenerator` are old APIs. The notebook also expects image folders (`data/train`) that are not present, so those steps cannot be verified. |
| langchain_readme | langchain_readme.md | https://raw.githubusercontent.com/patmejia/langchain/main/README.md | 2026-11-23 | LangChain | Imports such as `from langchain.llms import OpenAI` and `from langchain.chat_models import ChatOpenAI` moved to other packages. Every run needs an OpenAI key, so those steps can only be labelled "Can't verify" at best. |
| openai_tds | openai_tds.html | https://towardsdatascience.com/cracking-open-the-openai-python-api-230e4cae7971/ | 2026-11-23 | openai | Every request uses `openai.ChatCompletion.create`, removed in openai 1.0. All of them need an API key. |
| openai_stackoverflow | openai_stackoverflow.json | https://stackoverflow.com/a/77292230 | 2026-11-23 | openai | The python block calls `openai.ChatCompletion.create`, removed in openai 1.0, and needs an API key. The two install blocks (`pip install --upgrade openai`, `npm update openai`) are shell and are not run. |

## Notes

- `openai_stackoverflow.json` is the raw Stack Exchange API response (`/2.3/answers/77292230?site=stackoverflow&filter=withbody`) for one answer, not a web page, because stackoverflow.com answers curl with a Cloudflare check page. The code is in `<pre><code>` inside the `body` field. `title` in the expected file is the question's title; the answer is by Rok Benko, CC BY-SA 4.0, created 2023-10-14 UTC.
- `openai_tds` is a Towards Data Science article. Its code blocks are `<pre><code>` with one `<span class="line">` per line and no newline characters between the spans, so an extractor has to join the spans with newlines.
- `openai_so.html` and `openai_so.expected.json` are obsolete leftovers (identical to `openai_tds.*`). The user should delete them.
- `sklearn_boston` is the second pick. The GeeksforGeeks Boston page was fetched first but had been rewritten to use `fetch_openml`, so it was dropped.
- `langchain_readme` is a README from a personal repository (`patmejia/langchain`), not the official LangChain repository. Every official README tried (v0.0.1 to v0.1.0) had no Python code blocks. One block contains a stray blank line inside a string literal, as published.
- `keras_notebook` code cell 7 starts with a comment, then `!pip install pillow` and a `!KERAS_BACKEND=...` shell escape. It is labelled `python` because it is a notebook code cell. The packages list includes `pillow` from that pip line. One empty trailing code cell is left out of `steps`.
- `sklearn_boston`: blocks 2, 3, 7 and 9 (zero-based) have printed output pasted after the code in the same block. They are labelled `python`.
- `openai_tds`: the code does `from sk import my_sk`, a local module that is not on PyPI, so `packages` lists only `openai`. Blocks 2 and 3 (zero-based) end with a `"""Output: ..."""` string inside the code.
- `published` is set only where the page states it: GeeksforGeeks and DataScience+ via the page's JSON-LD or meta tags, Towards Data Science via its meta tags. The notebook and README give no date.

## Video replies

These are raw Gemini Interactions API replies for a YouTube tutorial, not video content. Each `<name>.json` is the reply as the API sent it. The matching `<name>.request.json` holds the exact request body without the key. All three were captured on 2026-11-24 with `python -m scripts.capture_video_reply`, in `backend/tests/fixtures/video/`.

The video is Krish Naik, "OpenAI API With Python Tutorial 1-ChatCompletion API and Completion API" (2023). It uses openai 0.27.8.

| File | Video URL | Clip window | Captured | Model | What it holds |
| --- | --- | --- | --- | --- | --- |
| CbpsDMwFG2g_0-300.json | https://www.youtube.com/watch?v=CbpsDMwFG2g | 0 to 300 s | 2026-11-24 | gemini-3.6-flash | Only shell setup: `conda create -p venv python=3.9 -y` at 153 s, `conda activate venv/` at 240 s, `pip install -r requirements.txt` at 265 s. |
| CbpsDMwFG2g_300-600.json | https://www.youtube.com/watch?v=CbpsDMwFG2g | 300 to 600 s | 2026-11-24 | gemini-3.6-flash | The requirements.txt contents (`openai`, `pandas`, `numpy`, `python-dotenv`) labelled shell, pip output, a shell line that still carries the Windows prompt, and the `.env` line `OPENAI_API_KEY="sk"` labelled shell. The pip output shows openai 0.27.8. |
| CbpsDMwFG2g_600-900.json | https://www.youtube.com/watch?v=CbpsDMwFG2g | 600 to 900 s | 2026-11-24 | gemini-3.6-flash | `pip install ipykernel` (703 s), `1+1` with output `2` (750 s), the openai and dotenv imports (759 s), and `pd.DataFrame(openai.Model.list()['data'])` (849 s), which breaks because `openai.Model` was removed in openai 1.0. |

## YouTube description and comment metadata

`youtube/CbpsDMwFG2g.videos.json` and `youtube/CbpsDMwFG2g.comments.json` are real replies for https://www.youtube.com/watch?v=CbpsDMwFG2g, captured on 2026-11-24 with `python -m scripts.capture_youtube_meta`. The description links one code source, `https://github.com/krishnaik06/OPENAI-API-Tutorials` (a repo whose README has no code), plus 11 YouTube links. Of the 49 comments, none are by the video's author. No test reads these files yet.

The inline replies in
`tests/test_youtube_capture.py` and the matching link-reader tests are marked
`# hand-made, replace with capture` and follow the official `videos.list` and
`commentThreads.list` shapes. They are synthetic test data, not captured replies.

Claude can run `python -m scripts.capture_youtube_meta VIDEO_URL` from `backend/`
with `YOUTUBE_API_KEY` configured. It saves the unmodified API response bytes as
`youtube/<ID>.videos.json` and `youtube/<ID>.comments.json`, refuses to overwrite
existing captures, and refuses any reply containing the configured key. A
`commentsDisabled` error reply is preserved as the comments fixture.

## Evidence pages (pandas release notes)

`evidence/pandas_whatsnew_index.html` and `evidence/pandas_whatsnew_v2.0.0.html` are trimmed copies of https://pandas.pydata.org/docs/whatsnew/index.html and https://pandas.pydata.org/docs/whatsnew/v2.0.0.html, fetched on 2026-11-24. The index keeps only the toctree links to the 2.x and 3.x version pages. The v2.0.0 page keeps the title and three items of "Removal of prior version deprecations/changes", including the `DataFrame.append()` removal note that points to `concat()`.

`evidence/pandas_whatsnew_v3.1.0.html` is a trimmed copy of https://pandas.pydata.org/docs/whatsnew/v3.1.0.html, fetched on 2026-11-24. It keeps the title and the `MultiIndex.union()` and two `HDFStore.append()` bug-fix lines, which mention `append` without `DataFrame.append`.
