from datetime import datetime,timedelta
import getpass
sys_user = getpass.getuser()

import sys
sys.path.append(f"/home/{sys_user}/gtp/backend/")

from airflow.decorators import dag, task
from src.db_connector import DbConnector
import src.misc.airtable_functions as at
from eth_utils import to_checksum_address


### DAG
default_args = {
    'owner' : 'lorenz',
    'retries' : 2,
    'email' : ['lorenz@growthepie.xyz', 'manish@growthepie.xyz', 'matthias@growthepie.xyz'],
    'email_on_failure': True,
    'retry_delay' : timedelta(minutes=15)
}

@dag(
    default_args=default_args,
    dag_id = 'dag_unlabelled_contracts_airtable',
    description = 'Update Airtable for contract labelling',
    start_date = datetime(2023,9,10),
    schedule = '00 02 * * *'
)


def etl():
    
    @task()
    def read_airtable():
        # read current airtable
        df = at.read_all_airtable()
        if df is None:
            print("Nothing to upload")
        else:
            df['added_on_time'] = datetime.now()
            df.set_index(['address', 'origin_key'], inplace=True)
            # initialize db connection
            db_connector = DbConnector()
            db_connector.upsert_table('blockspace_labels' ,df)

    @task()
    def write_airtable():
        # delete every row in airtable
        at.clear_all_airtable()
        # db connection
        db_connector = DbConnector()
        # get top unlabelled contracts
        df = db_connector.get_unlabelled_contracts('20', '30')
        df['address'] = df['address'].apply(lambda x: to_checksum_address('0x' + bytes(x).hex()))
        # write to airtable
        at.push_to_airtable(df)

    task1 = read_airtable()
    task2 = write_airtable()

    # Set task dependencies
    task1 >> task2

etl()

