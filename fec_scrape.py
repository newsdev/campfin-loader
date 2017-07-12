import requests
import os
import datetime
import pytz

from loader import loader


def scrape_filings_from_api(fec_api_key, start_date=None, end_date=None, comm_list=None):

    """
    if start_date, end_date or comm_list is provided, we'll only look for filings
    matching those specifications, otherwise we'll look for all
    Dates in format 20170713
    """
    filings = {}
    committees = []

    #TODO: factor out bad filing and bad committee lists
    bad_filings = []
    bad_committees = [
        'C00401224'
    ]

    url = "https://api.open.fec.gov/v1/efile/filings/?per_page=100&sort=-receipt_date"
    url += "&api_key={}".format(fec_api_key)
    if start_date:
        url += "&min_receipt_date={}".format(start_date)
    if end_date:
        url += "&max_receipt_date={}".format(end_date)
    if comm_list:
        for c in comm_list:
            url += "&committee_id={}".format(c)

    page = 1
    while True:
        print(url)
        resp = requests.get(url+"&page={}".format(page))
        page += 1
        files = resp.json()
        results = files['results']
        if len(results) == 0:
            break
        for f in results:
            if f['committee_id'].strip() in bad_committees:
                continue
            if int(f['file_number']) in [int(x) for x in bad_filings]:
                continue
            filings[f['file_number']] = "http://docquery.fec.gov/cgi-bin/forms/{}/{}".format(f['committee_id'],f['file_number'])


    return filings

def load_filings(start_date=None, end_date=None, comm_list=None):
    fec_api_key = os.environ.get('FEC_API_KEY')
    if not fec_api_key:
        print("no fec api key, can't scrape")
        return

    filing_list = scrape_filings_from_api(fec_api_key, start_date, end_date, comm_list)

    db_name = os.environ.get('DB_NAME')
    db_user = os.environ.get('DB_USER')
    db_host = os.environ.get('DB_HOST')
    db_password = os.environ.get('DB_PASSWORD')


    #create a loader with your credentials (mine are stored in environment variables)
    l = loader.Loader(db_name, db_host, db_user, db_password)
    l.load_filing_list(filing_list, fec_api_key)

def load_recent_filings():
    #load filings from past 2 days:
    fec_time=pytz.timezone('US/Eastern') #fec time is eastern
    start = datetime.datetime.now(fec_time) - datetime.timedelta(days=2)
    start_parsed = start.strftime('%Y%m%d')
    end = datetime.datetime.now(fec_time) + datetime.timedelta(days=1)
    end_parsed = end.strftime('%Y%m%d')
    load_filings(start_parsed, end_parsed)
