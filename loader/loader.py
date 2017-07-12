import os
import csv
import io
import psycopg2
import psycopg2.extras
import requests

from pyfec import form
from pyfec import filing

#TODO index the shit out of these tables


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

        cur = self.db_connection.cursor(cursor_factory=psycopg2.extras.DictCursor)

        if check_cols:
            if len(self.check_columns('filing')) > 0:
                return False

        #confirm we haven't already loaded this filing
        cur.execute("select * from filing where filing_id = {}".format(filing_id))
        rows = cur.fetchall()
        if len(rows) > 0:
            print("We've already loaded filing {}".format(filing_id))
            #pull up the filing anyway if the transactions errored before
            if rows[0]['transactions_status'] in ['error', 'not_loaded']:
                print("But the transactions are not loaded so we're going to pull up the filing and try again")
                return filing.Filing(filing_id)
            if rows[0]['transactions_status'] == 'loading':
                print("And the transactions are still loading from a previous run, if this doesn't resolve within a few minutes, check to confirm everything's OK")
            return
        try:
            f1 = filing.Filing(filing_id)
        except NotImplementedError:
            print("This filing type is not implemented in pyfec, not importing filing {}".format(filing_id))
            return
        except Exception as e:
            print("unexpected error trying to load filing {}: {}".format(filing_id, e))
            return
        filing_fields = f1.fields
        #check for previous filings that need to be marked as amended
        if not filing_fields:
            #if the field is unparseable (because it's not in allowed forms)
            #it returns {} for filing_fields. We should probably fix this in pyfec
            #but for now, this will work
            print("unparseable form")
            return
        print(filing_fields['form_type'])
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
        filing_keys = filing_fields.keys()
        col_list = ', '.join(filing_keys) + ", superseded_by_amendment, covered_by_periodic_filing, newest, transactions_status"
        vals = [filing_fields[k] for k in filing_keys]
        vals = vals + [False, False, True, "not loaded"]
        vals = tuple(vals)
        val_subs = ', '.join('%s' for v in vals)
        stmt = 'insert into filing ({}) values ({})'.format(col_list, val_subs)
        cur.execute(stmt, vals)
        self.db_connection.commit()

        return f1

    def find_most_recent_periodic(self, fec_id):
        #finds the most recent periodic filing for the committee specified in fec_id
        if not self.db_connection:
            self.connect_to_db()

    def load_single_filing(self, filing_id):
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

    def drop_temp_tables(self, filing_id):
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()
        for t in ['receipt', 'expenditure', 'independent_expenditure']:
            stmt = 'drop table if exists {}_{}_temp;'.format(t, filing_id)
            cur.execute(stmt)

        self.db_connection.commit()


    def run_copy_statement(self, filing_id, rows, table, cur):
        sked_string = io.StringIO()
        fieldnames = [f['field'] for f in self.get_fields_from_file(table)]
        writer = csv.DictWriter(sked_string, fieldnames)
        writer.writerows(rows)
        sked_string.seek(0)
        copy_stmt = "COPY {}_{}_temp FROM STDIN WITH (FORMAT CSV)".format(table, filing_id)
        cur.copy_expert(copy_stmt, sked_string)

    def get_insert_statement(self, filing_id, committee_name, table):
        fieldnames = [f['field'] for f in self.get_fields_from_file(table)]
        stmt = "insert into {table} ({fields}, filing_id, committee_name, superseded_by_amendment, covered_by_periodic_filing, newest)"
        stmt += " (select {fields}, %s, %s, %s, %s, %s from {table}_{filing_id}_temp)"
        stmt = stmt.format(table=table, fields=','.join(fieldnames), filing_id=filing_id)
        args = (filing_id, committee_name, False, False, True)
        return stmt, args

    def set_filing_status(self, filing_id, status):
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()
        stmt = "update filing set transactions_status='{}' where filing_id={}".format(status, filing_id)
        cur.execute(stmt)

        self.db_connection.commit()

    def load_lines(self, f=None, filing_id=None, line_type=None, check_cols=True):
        if not filing_id and not f:
            print("Specify either a filing or a filing_id")
            return False
        elif not f:
            f = filing.Filing(filing_id)

        committee_name = f.fields['committee_name']


        if not self.db_connection:
            self.connect_to_db()
        
        cur = self.db_connection.cursor()
        stmt = "select transactions_status from filing where filing_id={}".format(f.filing_id)
        cur.execute(stmt)
        if cur.fetchone()[0] in ['not loaded', 'error']:
            self.set_filing_status(f.filing_id, 'loading')
            
            try:
                self.run_copy_statement(f.filing_id, f.get_skeda(), 'receipt', cur)
                self.run_copy_statement(f.filing_id, f.get_skedb(), 'expenditure', cur)
                self.run_copy_statement(f.filing_id, f.get_skede(), 'independent_expenditure', cur)
            except Exception as e:
                print("load failed")
                print(e)
                self.set_filing_status(f.filing_id, 'error')
                return False

            try:
                #separating out the commit to make it clearer when there's a data problem vs a db problem
                self.db_connection.commit()
            except Exception as e:
                print("load failed")
                print(e)
                self.set_filing_status(f.filing_id, 'error')
                return False
            try:
                for transaction_type in ['receipt', 'expenditure', 'independent_expenditure']:
                    stmt, args = self.get_insert_statement(f.filing_id, committee_name, transaction_type)
                    cur.execute(stmt, args)
                self.db_connection.commit()

            except Exception as e:
                print("load failed")
                print(e)
                self.set_filing_status(f.filing_id, 'error')
                self.drop_temp_tables(f.filing_id)
                return False

            self.set_filing_status(f.filing_id, 'loaded')
            return True


        else:
            return False

    def complete_load(self, filing_id, fec_api_key=None, check_tables=True):
        #does the complete load process for a single filing
        
        if check_tables:
            #making this optional so we can skip it if we're doing a lot
            #of filings in a row with the same loader to save time
            self.create_or_alter_tables('filing')
            self.create_or_alter_tables('committee')

        #TODO we should probably make clear when we get the summary but fail on the transactions
        f  = self.load_filing_summary(filing_id, True)
        if f:
            if f.fields['form_type'] in ['F99']:
                #otherwise we try to parse the text of F99s and that's bad, we might want to factor out a bad_forms list somewhere
                print("we only load headers for forms of type {}".format(f.fields['form_type']))
                return
            #if this is None, we already have this filing loaded, yay!
            self.load_committee_details(f.fields['fec_id'], f.fields['committee_name'], fec_api_key)

            self.add_most_recent_periodic(f.fields['fec_id'])

            self.prep_transaction_tables(filing_id)

            lines_loaded = self.load_lines(f=f)
            if lines_loaded:
                print("lines successfully loaded for filing {}".format(filing_id))
            else:
                print("lines failed for filing {}".format(filing_id))

            self.drop_temp_tables(filing_id=filing_id)

        else:
            print("did not load summary or transactions for filing {}".format(filing_id))

    def load_filing_list(self, filing_ids, fec_api_key):
        #filing_ids is a list of filings

        #check the big tables once so we cab skip it on each individual filing
        self.create_or_alter_tables('filing')
        self.create_or_alter_tables('committee')

        for filing_id in filing_ids:
            self.complete_load(filing_id, fec_api_key=fec_api_key, check_tables=False)

