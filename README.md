# Credit Diligence Lab

A Python prototype for a lender's first pass through public borrower documents.
It searches the full Alphabet FY2025 Form 10-K, supplies the five highest-ranked
page passages to an LLM, and returns a JSON answer, credit implications,
missing information, and citations. Every quote is checked against a line
actually supplied to the model. Citations include PDF page links.

## Import the full filing

```powershell
uv run --python 3.13 ingest.py
```

The importer downloads the annual report linked from
[Alphabet's investor site](https://abc.xyz/investor/annual-meeting/default.aspx),
verifies the PDF's SHA-256, and extracts all 103 Form 10-K pages (PDF pages
5-107). The shareholder letter and closing directory sit outside the 10-K.
The SEC blocked the automated HTML download, so the importer uses the
issuer's official PDF copy.

Page passages preserve table rows and comparative year headers. The corpus
records section headings, global source line numbers, the reporting date,
and the PDF checksum. Source files and the generated corpus stay in local,
ignored data paths. Re-run the importer to reproduce them.

Retrieval uses local BM25 keyword scoring with a small financial vocabulary
mapping, including maturity/matures and revolver/revolving. Each page stays
together so its table headers and rows remain available. Use `--top-k` to
retrieve between 1 and 10 passages.

## Run with your ChatGPT plan

Use [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer. The script's
inline dependency list installs the pinned sign-in dependencies automatically.

```powershell
uv run --python 3.13 analyze.py --login
uv run --python 3.13 analyze.py --chatgpt
uv run --python 3.13 analyze.py --chatgpt "What is the borrower's maximum permitted net leverage ratio?"
```

The first command opens Continue with ChatGPT in your browser. Review the
requested permissions and authorize this app to use your plan. Eligible Plus
and Pro accounts can use their subscription allowance through
[OpenAI's official sign-in flow](https://developers.openai.com/siwc/token-sharing-open-source/sign-in).
Requests count toward your plan's shared usage limits and any credits you
authorize in [Manage usage](https://chatgpt.com/settings/usage).

The script discovers your account's models and defaults to the first listed
model. Use `--models` to list choices, then add `--model MODEL_SLUG` alongside
`--chatgpt` to choose one.

Saved connections live in the ignored `.chatgpt/` directory. Windows encrypts
credentials using your Windows account's DPAPI key. On Unix, files have
owner-only permissions. `--accounts` lists connections and `--select-account ID`
switches the active connection. Use `--login --new-account` to add another
account or workspace, and `--logout` to revoke the active session.

## Run with an OpenAI API key

The original API-key path uses the Python standard library and a funded
OpenAI API project. In PowerShell, the first command prompts for the key
without echoing it or adding it to command history.

```powershell
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenAI API key' -AsSecureString)).Password
python analyze.py
python analyze.py "What is the borrower's maximum permitted net leverage ratio?"
```

The default question retrieves evidence from
[Alphabet's FY2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm).
The source shows $3.0 billion of combined 2026 and 2027 principal payments and
a $4.0 billion revolver scheduled to expire in April 2026. The leverage
question returns `insufficient_evidence`. Both behaviors were verified in live
ChatGPT plan runs with GPT-6.1 Sol.

To inspect retrieval locally, including the supplied source lines:

```powershell
uv run --offline --python 3.13 analyze.py --retrieve-only "What debt matures over the next two years?"
```

Use `--excerpt` to run against the original Night 1 debt excerpt.

For API-key runs, `usage.estimated_cost_usd` applies
[GPT-5.4 Mini's published standard rates](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
to the API's reported uncached input, cached input, and output tokens. ChatGPT
plan runs report `billing_source: "chatgpt_plan"` and
`estimated_cost_usd: null`, with a link to manage subscription usage.

## Checks

```powershell
uv run --python 3.13 --with "PyJWT[crypto]==2.15.1" --with "filelock==4.2.10" python -m unittest discover -s tests -v
uv run --offline --python 3.13 evaluate.py --retrieval-only
uv run --offline --python 3.13 evaluate.py --chatgpt
```

The local test suite covers authentication, full-filing page coverage, table
rows, retrieval recall, and rejection of citations outside retrieved context.
`evaluation/questions.json` fixes the five benchmark questions and expected
amounts, dates, statuses, and source pages. The offline benchmark checks the
four answerable questions; the missing-covenant case is marked inapplicable
for retrieval recall.

The live benchmark saves answers, citations, token usage, and check results
to `evaluation/night2-results.json`. Its checks cover principal maturities,
revolver capacity and expirations, operating cash flow, cash resources, and
an unavailable leverage covenant. Broader credit conclusions need manual review.

## Current scope

Night 2 covers one full filing with five curated answer checks. Keyword
retrieval can miss relevant passages for unfamiliar wording. An
`insufficient_evidence` answer describes the retrieved context's limits.
The next build adds an SEC-filed credit agreement for covenant analysis.
