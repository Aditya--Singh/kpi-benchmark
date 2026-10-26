# Test fixtures

`text/` holds the text layer of five quarterly presentations (PNB Q2 FY27, SBI Q1 FY27, Bank of Baroda Q1 FY27,
Canara Bank Q2 FY27, Union Bank Q2 FY27) as published on each bank's investor-relations page, one page per
`===PAGE===` block. `truth.json` holds the headline values checked by hand against those pages.
`pdf/` holds the same text re-rendered as small PDFs so the whole pipeline can be exercised offline.

These files are used only by `python -m app.cli selftest`, which runs in a temporary folder and deletes it
afterwards. They are never loaded into the application's database.
