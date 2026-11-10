# Credit Diligence Lab

A Python prototype for a lender's first pass through public borrower documents.
It asks one credit question about a debt footnote and returns a JSON answer,
the credit implication, missing information, and evidence with line numbers.
The script checks that every cited quote appears on its claimed line.

## Run

Use Python 3.10 or newer. The script uses the standard library and makes one
OpenAI Responses API request per run. Set `OPENAI_API_KEY` in your shell using
a key from a project with API access. In PowerShell, the first command prompts
for the key without echoing it or adding it to command history.

```powershell
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenAI API key' -AsSecureString)).Password
python analyze.py
python analyze.py "What is the borrower's maximum permitted net leverage ratio?"
```

The default question uses [Alphabet's FY2025 Form 10-K, Note 6](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm).
The source shows $3.0 billion of combined 2026 and 2027 principal payments and
a $4.0 billion revolver scheduled to expire in April 2026. The leverage
question should return `insufficient_evidence`. These are manual checks
against the excerpt.

`usage.estimated_cost_usd` applies [GPT-5.4 Mini's published standard rates](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
to the API's reported uncached input, cached input, and output tokens.

## Scope

Night 1 sends the selected excerpt directly. The next build adds the full
filing and an SEC-filed credit agreement, retrieves relevant passages, and
checks answers against a small set of known debt and covenant questions.
Use public documents for this demo.
