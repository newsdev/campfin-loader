import os
import csv
import psycopg2

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

    def get_fields_from_file(self, tablename):
        cwd = os.path.dirname(os.path.realpath(__file__))
        p = cwd+"/fields/"+tablename+".csv"
        try:
            f = open(p, 'r')

        except FileNotFoundError:
            print("file {} not found".format(p))
            return None
        
        reader = csv.DictReader(f)
        return reader
        

    def create_or_alter_tables(self, table):
        if not self.db_connection:
            self.connect_to_db()

        cur = self.db_connection.cursor()

        reader = self.get_fields_from_file(table)
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

        if check_cols:
            if not self.check_columns('filing'):
                return False

        f1 = filing.Filing(filing_id)
        filing_fields = f1.fields
        #do whatever magic needs to happen here - look for amendments, calculate burn rate, etc
        #then load summary

    def load_lines(self, filing_id, line_type=None, check_cols=True):
        pass
        #if line_type is none, do em all
        #otherwise linetype should be a, b, e or other

