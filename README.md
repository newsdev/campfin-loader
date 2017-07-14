# campfin-loader
A new data loader for campaign finance that is totally separate from the front end

This module uses `nyt-pyfec` to parse .fec files and dumps the results in a postgres database. It doesn't stray far from the FEC's raw data, but it does mark filings as amended or covered by a periodic filing (and marks newest=False in those cases).

To query valid transactions, include `WHERE newest=true` in any queries. If you want totals that are close to itemized totals reported by the FEC, also include `where memo_entry=false`

## quickstart

1. Use python3
1. Install postgres
1. Create a postgres database
1. If using virtualenvwrapper (which is STRONGLY RECOMMENDED):  `mkvirutalenv campfin-loader --python $(which python3)`
1. `pip install -r requirements.txt`
1. Add your database credentials as environment varibales `DB_NAME`, `DB_HOST`, `DB_USER` and `DB_PASSWORD` (the latter defaults to None). Recommended: do this in `$VIRTUAL_ENV/bin/postactivate` if you're using virtualenvwrapper
1. (Get an FEC API)[keyhttps://api.data.gov/signup/]. (Or submit a pull request to scrape the page!)
1. Use the methods in `fec_scrape.py`. The easiest way to scrape local filings right now is to run `python -c 'from fec_scrape import load_recent_filings; load_recent_filings()'`. Commandline interface hopefully coming soon.
