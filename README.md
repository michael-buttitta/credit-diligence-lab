# Credit Diligence Lab

A Python prototype for a lender's first pass through public borrower documents.
It asks one credit question about a debt footnote and returns a JSON answer,
the credit implication, missing information, and evidence with line numbers.
The script checks that every cited quote appears on its claimed line.

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

The default question uses [Alphabet's FY2025 Form 10-K, Note 6](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm).
The source shows $3.0 billion of combined 2026 and 2027 principal payments and
a $4.0 billion revolver scheduled to expire in April 2026. The leverage
question returns `insufficient_evidence`. Both behaviors were verified in live
ChatGPT plan runs with GPT-6.1 Sol.

For API-key runs, `usage.estimated_cost_usd` applies
[GPT-5.4 Mini's published standard rates](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
to the API's reported uncached input, cached input, and output tokens. ChatGPT
plan runs report `billing_source: "chatgpt_plan"` and
`estimated_cost_usd: null`, with a link to manage subscription usage.

## Checks

```powershell
uv run --python 3.13 --with "PyJWT[crypto]==2.15.1" --with "filelock==4.2.10" python -m unittest discover -s tests -v
```

## Scope

Night 1 sends the selected excerpt directly. The next build adds the full
filing and an SEC-filed credit agreement, retrieves relevant passages, and
checks answers against a small set of known debt and covenant questions.
Use public documents for this demo.
