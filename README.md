# campfin-loader
A new data loader for campaign finance that is totally separate from the front end

This module uses `nyt-pyfec` to parse .fec files and dumps the results in a postgres database. It doesn't stray far from the FEC's raw data, but it does mark filings as amended or covered by a periodic filing (and marks newest=False in those cases).

To query valid transactions, include `WHERE newest=true` in any queries. If you want totals that are close to itemized totals reported by the FEC, also include `where memo_entry=false`

## getting started

1. Install postgres
1. Create a postgres database
1. If using virtualenvwrapper (which is STRONGLY RECOMMENDED):  `mkvirutalenv campfin-loader --python $(which python3)`
1. `pip install -r requirements.txt`
