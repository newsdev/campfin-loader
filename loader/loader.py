import os
import csv
import io
import psycopg2
import psycopg2.extras
import requests

from pyfec import form
from pyfec import filing

class Loader(object):
    
    def __init__(self, db_name, db_host, db_user, db_password=None):
        self.db_name = db_name
        self.db_host = db_host
        self.db_user = db_user
        self.db_password = db_password
        self.db_connection = None
        self.form = None

    def connect_to_db(self):
        try:
            self.db_connection = psycopg2.connect(dbname=self.db_name,
                user=self.db_user,
                password=self.db_password,
                host=self.db_host)
        except psycopg2.Error as e:
            print('failed to connect to db')
            pass
            #we're actually going to want to log this error when we get around to logging

    def get_fields_from_file(self, tablename, transaction_table=False):
        cwd = os.path.dirname(os.path.realpath(__file__))
        p = cwd+"/fields/"+tablename+".csv"
        try:
            f = open(p, 'r')

        except FileNotFoundError:
            print("file {} not found".format(p))
            return None
        
        reader = csv.DictReader(f)
        lines = [line for line in reader]

        if transaction_table:
            #this is sneaky, I'm reusing this method, because I want to do the same thing and I'm kind of a bad person
            transaction_lines = self.get_fields_from_file("transaction_extra", False)
            lines = transaction_lines + lines

        return lines


    def create_or_alter_tables(self, table):
        transaction_table = table in ["receipt", "expenditure", "independent_expenditure"]

        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()

        #if it's a transaction table, this part creates the real table with the transaction extras in it
        reader = self.get_fields_from_file(table, transaction_table)
        assert reader, "Unknown table name {}".format(table)


        create_stmt_fields = ', '.join(['{} {}'.format(line['field'],line['desc']) for line in reader])

        #assemble create_table statement
        stmt = "create table if not exists {} ({})".format(table, create_stmt_fields)

        #run create_table statement
        cur.execute(stmt)
        self.db_connection.commit()

        #check for missing fields
        missing_cols = self.check_columns(table)

        #add missing fields
        if len(missing_cols) > 0:
            add_cols_stmt = ', '.join(['{} {}'.format(line['field'],line['desc']) for line in missing_cols])
            stmt = "alter table {} add {}".format(table, add_cols_stmt)
            cur.execute(stmt)
            self.db_connection.commit()


    def create_filing_temp_tables(self, filing_id):
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()

        #drops and creates the temp table
        for table in ['receipt', 'expenditure', 'independent_expenditure']:
            table_name = "{}_{}_temp".format(table, filing_id)
            cur.execute("select * from information_schema.tables where table_name=%s", (table_name,))
            print(table_name)
            print(cur.fetchall())
            print(cur.rowcount)
            print(bool(cur.rowcount))
            if bool(cur.rowcount):
                #bool(cur.rowcount) is false iff there are zero rows
                print("temp tables already exist for this filing, it is probably in the process of being loaded")
                continue
            reader = self.get_fields_from_file(table, False)
            assert reader, "Unknown table name {}".format(table)


            create_stmt_fields = ', '.join(['{} {}'.format(line['field'],line['desc']) for line in reader])

            #assemble create_table statement
            stmt = "create table if not exists {} ({})".format(table_name, create_stmt_fields)

            #run create_table statement
            cur.execute(stmt)
            self.db_connection.commit()

    def prep_transaction_tables(self, filing_id):
        #check transaction status on filing table
        self.create_or_alter_tables('receipt')
        self.create_or_alter_tables('expenditure')
        self.create_or_alter_tables('independent_expenditure')
        self.create_filing_temp_tables(filing_id)


    def check_columns(self, table):
        """
        Checks to make sure columns in database are a superset of columns listed
        in the relevant table in the fields directory.
        Returns columns in file that are not in database table.
        Is agnostic to fields in database that are not in file.
        """

        #connect to database if needed:
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()
        columns = cur.execute("SELECT * FROM {} LIMIT 0".format(table))
        col_names = [desc[0] for desc in cur.description]
        reader = self.get_fields_from_file(table)
        assert reader, "Unknown table name {}".format(table)
        file_fields_missing = []
        for f in reader:
            if f['field'] not in col_names:
                file_fields_missing.append(f)
        return file_fields_missing


    def load_filing_summary(self, filing_id, check_cols=True):
        #connect to database if needed:
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()

        if check_cols:
            if len(self.check_columns('filing')) > 0:
                return False

        #confirm we haven't already loaded this filing
        cur.execute("select * from filing where filing_id = {}".format(filing_id))
        rows = cur.fetchall()
        if len(rows) > 0:
            print("We've already loaded filing {}".format(filing_id))
            return

        f1 = filing.Filing(filing_id)
        filing_fields = f1.fields

        #check for previous filings that need to be marked as amended
        if filing_fields['is_amendment']:
            amends_filing = filing_fields['amends_filing']
            #mark that filing as amended and newest=False
            stmt = "update filing set superseded_by_amendment = %s, amended_by = %s, newest = %s where filing_id='{}'".format(amends_filing)
            cur.execute(stmt, (True, filing_id, False))
            #TODO: edge case: if a filer files a second amendment but refers only back to the original filing, we're sometimes in trouble, we need to check for this
        
        #check for previous filings that need to be marked as covered by periodic
        if f1.is_periodic:
            print("this one's periodic, do stuff here")
            #TODO: do this. there's some logic in campfin that we should follow in terms of deactivating transactions. This gets gross

        #do load
        print(filing_fields)
        filing_keys = filing_fields.keys()
        col_list = ', '.join(filing_keys) + ", superseded_by_amendment, covered_by_periodic_filing, newest, filing_status"
        vals = [filing_fields[k] for k in filing_keys]
        vals = vals + [False, False, True, "not loaded"]
        vals = tuple(vals)
        val_subs = ', '.join('%s' for v in vals)
        stmt = 'insert into filing ({}) values ({})'.format(col_list, val_subs)
        cur.execute(stmt, vals)
        self.db_connection.commit()

    def find_most_recent_periodic(self, fec_id):
        #finds the most recent periodic filing for the committee specified in fec_id
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        stmt = """select filing_id, coverage_to_date from filing
                where fec_id = '{}' and is_periodic = True
                order by coverage_to_date desc"""
        cur.execute(stmt.format(fec_id))
        most_recent = cur.fetchone()
        if most_recent:
            return (most_recent['filing_id'], most_recent['coverage_to_date'])
        return None

    def add_most_recent_periodic(self, fec_id):
        print("searching for most recent periodic")
        most_recent = self.find_most_recent_periodic(fec_id)
        if most_recent:
            if not self.db_connection:
                self.connect_to_db()
            cur = self.db_connection.cursor()
            print(most_recent)
            stmt = "update committee set most_recent_periodic = %s, most_recent_coverage_date = %s where fec_id = '{}'".format(fec_id)
            print(cur.mogrify(stmt,most_recent))
            cur.execute(stmt, most_recent)
            self.db_connection.commit()
            print("added most recent periodic")



    def load_committee_details(self, fec_id, committee_name, fec_api_key=None):
        #when a new committee is encountered, hit the FEC API and initialize the data
        #if we're out of API calls, add it based on what's available in the filing
        #and mark as uninitialized

        #TODO do we want a flag for candidate vs other committee so we don't have to store the logic in a frontend?


        #check if committee exists and is loaded
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("select * from committee where fec_id = '{}'".format(fec_id))
        rows = cur.fetchall()
        if len(rows) > 1:
            print("this is very bad, we've got multiple committees with the same ID")
            return
        elif len(rows) == 1 and rows[0]['details_loaded']:
            #we already know about this committee and have loaded its details, so we're good to go
            return

        if not fec_api_key:
            if len(rows) == 1:
                #we already have the basics and have no api key, so don't do anything
                return
            #otherwise the committee doesn't exist but no api key is provided, load minimal details
            cur.execute("insert into committee (fec_id, committee_name, details_loaded) values (%s, %s, %s)", (fec_id, committee_name, False))
            self.db_connection.commit()
            return

        #find the committee details
        url = "https://api.open.fec.gov/v1/committee/{}/?api_key={}".format(fec_id, fec_api_key)
        r = requests.get(url)
        try:
            data = r.json()['results'][0]
        except IndexError:
            #this committee isn't in the API yet, it's probably brand new. Just load what we have with details_loaded=False
            if len(rows) == 0:
                cur.execute("insert into committee (fec_id, committee_name, details_loaded) values (%s, %s, %s)", (fec_id, committee_name, False))
                self.db_connection.commit()
            return

        except KeyError:
            print("We've used up our fec api calls, we'll load this committee's details another time")
            if len(rows) == 0:
                cur.execute("insert into committee (fec_id, committee_name, details_loaded) values (%s, %s, %s)", (fec_id, committee_name, False))
                self.db_connection.commit()
            return

        comm_fields = (fec_id, data['name'], data['committee_type'], data['designation'], data['filing_frequency'], True)
        if len(rows) == 0:
            cur.execute("insert into committee (fec_id, committee_name, committee_type, committee_designation, frequency, details_loaded) values (%s, %s, %s, %s, %s, %s)", comm_fields)
        else:
            cur.execute("update committee set fec_id = %s, committee_name = %s, committee_type = %s, committee_designation = %s, frequency = %s, details_loaded = %s where fec_id='{}'".format(fec_id), comm_fields)
        self.db_connection.commit()
        return


    def load_lines(self, f=None, filing_id=None, line_type=None, check_cols=True):
        if not filing_id and not f:
            print("Specify either a filing or a filing_id")
            return False
        elif not f:
            f = filing.Filing(filing_id)

        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()
        stmt = "select transactions_status from filing where filing_id={}".format(f.filing_id)
        cur.execute(stmt)
        if cur.fetchone()[0] in ['not loaded', 'error']:
            stmt = "update filing set transactions_status='loading' where filing_id={}".format(f.filing_id)
            cur.execute(stmt)
            self.db_connection.commit()

            #TODO wrap this all in a try and if it fails reset status to "error", if successful, reset status to "loaded"
            skeda_string = io.StringIO()
            fieldnames = [f['field'] for f in self.get_fields_from_file('receipt')]
            writer = csv.DictWriter(skeda_string, fieldnames)
            writer.writerows(f.get_skeda())
            skeda_string.seek(0)
            copy_stmt = "COPY receipt_{}_temp FROM STDIN WITH (FORMAT CSV)".format(f.filing_id)
            cur.copy_expert(copy_stmt, skeda_string)
            #TODO load skedb
            #TODO load skede

            self.db_connection.commit()
            stmt = "update filing set transactions_status='loading' where filing_id={}".format(f.filing_id)

        else:
            return False
        #the initial load happens in a bash script because it's way more efficient
        #I want to make it all doable via python, so I'm just running the script here.
