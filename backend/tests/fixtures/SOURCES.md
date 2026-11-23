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
