# casRequest

Automates submitting a CAS - CAMS+KFintech request on CAMS site:
Detailed statement, Specific Period from 01-Jan-2001 to today, for one or
more investors listed in a CSV file.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Google Chrome installed.

```
uv sync
```

## Create the CSV

Create a CSV file with `email` and `pan` columns — one row per investor
request. Column names are case-insensitive.

```csv
email,pan
investor1@example.com,ABCDE1234F
investor2@example.com,FGHIJ5678K
```

A template is provided at `sample_requests.csv` — copy it and fill in real
data:

```
cp sample_requests.csv my_requests.csv
```

The password and confirm-password fields are derived automatically from
each row's PAN (lowercased) — you don't need to include them in the CSV.

## Run

```
uv run python cas_request.py my_requests.csv
```

Add `--headless` to run without a visible browser window:

```
uv run python cas_request.py my_requests.csv --headless
```

The script processes each row: it loads the CAMS CAS page, accepts the
disclaimer, selects Detailed / Specific Period, sets the date range, fills
in the email/PAN/password fields, and clicks Submit.

**Note:** `*.csv` files (other than `sample_requests.csv`) are gitignored
since they contain PAN and email data — don't commit real investor data.
