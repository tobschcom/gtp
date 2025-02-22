import numpy as np
import boto3
import botocore
from web3 import Web3, HTTPProvider
from web3.middleware import geth_poa_middleware
import pandas as pd
from sqlalchemy import create_engine, exc
import os
import sys
import random
import time

# ---------------- Utility Functions ---------------------
def safe_float_conversion(x):
    try:
        if isinstance(x, str) and x.startswith('0x'):
            return float(int(x, 16))
        return float(x)
    except (ValueError, TypeError):
        return np.nan

def hex_to_int(hex_str):
    try:
        return int(hex_str, 16)
    except (ValueError, TypeError):
        return None 

# ---------------- Connection Functions ------------------
def connect_to_node(url):
    w3 = Web3(HTTPProvider(url))
    
    # Apply the geth POA middleware to the Web3 instance
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    
    if w3.is_connected():
        return w3
    else:
        raise ConnectionError("Failed to connect to the node.")

def connect_to_s3():
    try:
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        bucket_name = os.getenv("S3_LONG_TERM_BUCKET")

        if not aws_access_key_id or not aws_secret_access_key or not bucket_name:
            raise EnvironmentError("AWS access key ID, secret access key, or bucket name not found in environment variables.")

        s3 = boto3.client('s3',
                            aws_access_key_id=aws_access_key_id,
                            aws_secret_access_key=aws_secret_access_key)
        return s3, bucket_name
    except Exception as e:
        print("An error occurred while connecting to S3:", str(e))
        raise ConnectionError(f"An error occurred while connecting to S3: {str(e)}")

def check_s3_connection(s3_connection):
    return s3_connection is not None

def s3_file_exists(s3, file_key, bucket_name):
    try:
        s3.head_object(Bucket=bucket_name, Key=file_key)
        return True
    except botocore.exceptions.ClientError as e:
        # If the error code is 404 (Not Found), then the file doesn't exist.
        error_code = int(e.response['Error']['Code'])
        if error_code == 404:
            return False
        else:
            # Re-raise the exception if it's any other error.
            raise e

# ---------------- Data Processing Functions -------------
def prep_dataframe(df):
    # Ensure the required columns exist, filling with 0 if they don't
    required_columns = ['l1GasUsed', 'l1GasPrice', 'l1FeeScalar']
    for col in required_columns:
        if col not in df.columns:
            df[col] = 0

    # Define a mapping of old columns to new columns
    column_mapping = {
        'blockNumber': 'block_number',
        'hash': 'tx_hash',
        'from': 'from_address',
        'to': 'to_address',
        'gasPrice': 'gas_price',
        'gas': 'gas_limit',
        'gasUsed': 'gas_used',
        'value': 'value',
        'status': 'status',
        'input': 'empty_input',
        'l1GasUsed': 'l1_gas_used',
        'l1GasPrice': 'l1_gas_price',
        'l1FeeScalar': 'l1_fee_scalar',
        'block_timestamp': 'block_timestamp'
    }

    # Filter the dataframe to only include the relevant columns
    filtered_df = df[list(column_mapping.keys())]

    # Rename the columns based on the above mapping
    filtered_df = filtered_df.rename(columns=column_mapping)

    # Convert columns to numeric if they aren't already
    filtered_df['gas_price'] = pd.to_numeric(filtered_df['gas_price'], errors='coerce')
    filtered_df['gas_used'] = pd.to_numeric(filtered_df['gas_used'], errors='coerce')

    # Apply the safe conversion to the l1_gas_price column
    filtered_df['l1_gas_price'] = filtered_df['l1_gas_price'].apply(safe_float_conversion)
    filtered_df['l1_gas_price'] = filtered_df['l1_gas_price'].astype('float64')
    filtered_df['l1_gas_price'].fillna(0, inplace=True)
    
    # Handle 'l1_fee_scalar'
    filtered_df['l1_fee_scalar'].fillna('0', inplace=True)
    filtered_df['l1_fee_scalar'] = pd.to_numeric(filtered_df['l1_fee_scalar'], errors='coerce')

    # Handle 'l1_gas_used'
    filtered_df['l1_gas_used'] = filtered_df['l1_gas_used'].apply(hex_to_int)
    filtered_df['l1_gas_used'].fillna(0, inplace=True)

    # Calculating the tx_fee
    filtered_df['tx_fee'] = ((filtered_df['gas_price'] * filtered_df['gas_used']) + (filtered_df['l1_gas_used'] * filtered_df['l1_gas_price'] * filtered_df['l1_fee_scalar'])) / 1e18
    
    # Convert the 'l1_gas_price' column to eth
    filtered_df['l1_gas_price'] = filtered_df['l1_gas_price'].astype(float) / 1e18
    
    # Convert the 'input' column to boolean to indicate if it's empty or not
    filtered_df['empty_input'] = filtered_df['empty_input'].apply(lambda x: True if (x == '0x' or x == '') else False)

    # Convert block_timestamp to datetime
    filtered_df['block_timestamp'] = pd.to_datetime(df['block_timestamp'], unit='s')

    # status column: 1 if status is success, 0 if failed else -1
    filtered_df['status'] = filtered_df['status'].apply(lambda x: 1 if x == 1 else 0 if x == 0 else -1)

    # replace None in 'to_address' column with empty string
    if 'to_address' in filtered_df.columns:
        filtered_df['to_address'] = filtered_df['to_address'].fillna(np.nan)
        filtered_df['to_address'] = filtered_df['to_address'].replace('None', np.nan)

    # Handle bytea data type
    for col in ['tx_hash', 'to_address', 'from_address']:
        if col in filtered_df.columns:
            filtered_df[col] = filtered_df[col].str.replace('0x', '\\x', regex=False)
        else:
            print(f"Column {col} not found in dataframe.")             

    # gas_price column in eth
    filtered_df['gas_price'] = filtered_df['gas_price'].astype(float) / 1e18

    # value column divide by 1e18 to convert to eth
    filtered_df['value'] = filtered_df['value'].astype(float) / 1e18

    return filtered_df    

def prep_dataframe_scroll(df):
    # Define a mapping of old columns to new columns
    column_mapping = {
        'blockNumber': 'block_number',
        'hash': 'tx_hash',
        'from': 'from_address',
        'to': 'to_address',
        'gasPrice': 'gas_price',
        'gas': 'gas_limit',
        'gasUsed': 'gas_used',
        'value': 'value',
        'status': 'status',
        'input': 'empty_input',
        'l1Fee': 'l1_fee',
        'block_timestamp': 'block_timestamp'
    }

    # Filter the dataframe to only include the relevant columns
    filtered_df = df[list(column_mapping.keys())]

    # Rename the columns based on the above mapping
    filtered_df = filtered_df.rename(columns=column_mapping)

    filtered_df['l1_fee'] = filtered_df['l1_fee'].apply(lambda x: int(x, 16) / 1e18 if x.startswith('0x') else float(x) / 1e18)
    
    # Convert columns to numeric if they aren't already
    filtered_df['gas_price'] = pd.to_numeric(filtered_df['gas_price'], errors='coerce')
    filtered_df['gas_used'] = pd.to_numeric(filtered_df['gas_used'], errors='coerce')
    
    # Calculating the tx_fee
    filtered_df['tx_fee'] = (filtered_df['gas_price'] * filtered_df['gas_used']) / 1e18 + filtered_df['l1_fee']
    
    # Convert the 'input' column to boolean to indicate if it's empty or not
    filtered_df['empty_input'] = filtered_df['empty_input'].apply(lambda x: True if (x == '0x' or x == '') else False)

    # Convert block_timestamp to datetime
    filtered_df['block_timestamp'] = pd.to_datetime(df['block_timestamp'], unit='s')

    # status column: 1 if status is success, 0 if failed else -1
    filtered_df['status'] = filtered_df['status'].apply(lambda x: 1 if x == 1 else 0 if x == 0 else -1)
    
    # replace None in 'to_address' column with empty string
    if 'to_address' in filtered_df.columns:
        filtered_df['to_address'] = filtered_df['to_address'].fillna(np.nan)
        filtered_df['to_address'] = filtered_df['to_address'].replace('None', np.nan)
        
    # Handle bytea data type
    for col in ['tx_hash', 'to_address', 'from_address']:
        if col in filtered_df.columns:
            filtered_df[col] = filtered_df[col].str.replace('0x', '\\x', regex=False)
        else:
            print(f"Column {col} not found in dataframe.")

    # gas_price column in eth
    filtered_df['gas_price'] = filtered_df['gas_price'].astype(float) / 1e18

    # value column divide by 1e18 to convert to eth
    filtered_df['value'] = filtered_df['value'].astype(float) / 1e18

    return filtered_df  

def prep_dataframe_linea(df):
    # Define a mapping of old columns to new columns
    column_mapping = {
        'blockNumber': 'block_number',
        'hash': 'tx_hash',
        'from': 'from_address',
        'to': 'to_address',
        'gasPrice': 'gas_price',
        'gas': 'gas_limit',
        'gasUsed': 'gas_used',
        'value': 'value',
        'status': 'status',
        'input': 'empty_input',
        'block_timestamp': 'block_timestamp'
    }

    # Filter the dataframe to only include the relevant columns
    filtered_df = df[list(column_mapping.keys())]

    # Rename the columns based on the above mapping
    filtered_df = filtered_df.rename(columns=column_mapping)

    # Convert columns to numeric if they aren't already
    filtered_df['gas_price'] = pd.to_numeric(filtered_df['gas_price'], errors='coerce')
    filtered_df['gas_used'] = pd.to_numeric(filtered_df['gas_used'], errors='coerce')
    
    # Calculating the tx_fee
    filtered_df['tx_fee'] = (filtered_df['gas_price'] * filtered_df['gas_used'])  / 1e18
    
    # Convert the 'input' column to boolean to indicate if it's empty or not
    filtered_df['empty_input'] = filtered_df['empty_input'].apply(lambda x: True if (x == '0x' or x == '') else False)

    # Convert block_timestamp to datetime
    filtered_df['block_timestamp'] = pd.to_datetime(df['block_timestamp'], unit='s')

    # status column: 1 if status is success, 0 if failed else -1
    filtered_df['status'] = filtered_df['status'].apply(lambda x: 1 if x == 1 else 0 if x == 0 else -1)

    # replace None in 'to_address' column with empty string
    if 'to_address' in filtered_df.columns:
        filtered_df['to_address'] = filtered_df['to_address'].fillna(np.nan)
        filtered_df['to_address'] = filtered_df['to_address'].replace('None', np.nan)
        
    # Handle bytea data type
    for col in ['tx_hash', 'to_address', 'from_address']:
        if col in filtered_df.columns:
            filtered_df[col] = filtered_df[col].str.replace('0x', '\\x', regex=False)
        else:
            print(f"Column {col} not found in dataframe.")

    # gas_price column in eth
    filtered_df['gas_price'] = filtered_df['gas_price'].astype(float) / 1e18

    # value column divide by 1e18 to convert to eth
    filtered_df['value'] = filtered_df['value'].astype(float) / 1e18

    return filtered_df 

# ---------------- Error Handling -----------------------
class MaxWaitTimeExceededException(Exception):
    pass

def handle_retry_exception(current_start, current_end, base_wait_time):
    max_wait_time = 300  # Maximum wait time in seconds
    wait_time = min(max_wait_time, 2 * base_wait_time)

    # Check if max_wait_time is reached and raise an exception
    if wait_time >= max_wait_time:
        raise MaxWaitTimeExceededException(f"Maximum wait time exceeded for blocks {current_start} to {current_end}")

    # Add jitter
    jitter = random.uniform(0, wait_time * 0.1)
    wait_time += jitter
    formatted_wait_time = format(wait_time, ".2f")

    print(f"Retrying for blocks {current_start} to {current_end} after {formatted_wait_time} seconds.")
    time.sleep(wait_time)

    return wait_time

# ---------------- Database Interaction ------------------
def check_db_connection(db_connector):
    return db_connector is not None

def create_db_engine(db_user, db_password, db_host, db_port, db_name):
    print("Creating database engine...")
    try:
        # create connection to Postgres
        engine = create_engine(f'postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}')
        engine.connect()  # test connection
        return engine
    except exc.SQLAlchemyError as e:
        print("Error connecting to database. Check your database configurations.")
        print(e)
        sys.exit(1)

# ---------------- Data Interaction --------------------
def fetch_block_transaction_details(w3, block):
    transaction_details = []
    block_timestamp = block['timestamp']  # Get the block timestamp
    
    for tx in block['transactions']:
        tx_hash = tx['hash']
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        
        # Convert the receipt and transaction to dictionary if it is not
        if not isinstance(receipt, dict):
            receipt = dict(receipt)
        if not isinstance(tx, dict):
            tx = dict(tx)
        
        # Merge transaction and receipt dictionaries
        merged_dict = {**receipt, **tx}
        
        # Add or update specific fields
        merged_dict['hash'] = tx['hash'].hex()
        merged_dict['block_timestamp'] = block_timestamp
        
        # Add the transaction receipt dictionary to the list
        transaction_details.append(merged_dict)
        
    return transaction_details

def get_latest_block(w3):
    try:
        return w3.eth.block_number
    except Exception as e:
        print("An error occurred while fetching the latest block:", str(e))
        return None
    
def fetch_data_for_range(w3, block_start, block_end):
    print(f"Fetching data for blocks {block_start} to {block_end}...")
    all_transaction_details = []

    try:
        # Loop through each block in the range
        for block_num in range(block_start, block_end + 1):
            block = w3.eth.get_block(block_num, full_transactions=True)
            
            # Fetch transaction details for the block using the new function
            transaction_details = fetch_block_transaction_details(w3, block)
            
            all_transaction_details.extend(transaction_details)

        # Convert list of dictionaries to DataFrame
        df = pd.DataFrame(all_transaction_details)
        
        # if df doesn't have any records, then handle it gracefully
        if df.empty:
            print(f"No transactions found for blocks {block_start} to {block_end}.")
            return None  # Or return an empty df as: return pd.DataFrame()
        else:
            return df

    except Exception as e:
        raise e

def save_data_for_range(df, block_start, block_end, chain, s3_connection, bucket_name):
    # Convert any 'object' dtype columns to string
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                df[col] = df[col].apply(str)
            except Exception as e:
                raise e

    # Generate the filename
    filename = f"{chain}_tx_{block_start}_{block_end}.parquet"
    
    # Create S3 file path
    file_key = f"{chain}/{filename}"
    
    # Use the S3 functionality in pandas to write directly to S3
    s3_path = f"s3://{bucket_name}/{file_key}"
    df.to_parquet(s3_path, index=False)

    # Check if the file exists in S3
    if s3_file_exists(s3_connection, file_key, bucket_name):
        print(f"File {file_key} uploaded to S3 bucket {bucket_name}.")
    else:
        print(f"File {file_key} not found in S3 bucket {bucket_name}.")
        raise Exception(f"File {file_key} not uploaded to S3 bucket {bucket_name}. Stopping execution.")

def fetch_and_process_range(current_start, current_end, chain, w3, table_name, s3_connection, bucket_name, db_connector):
    base_wait_time = 5   # Base wait time in seconds
    while True:
        try:
            
            df = fetch_data_for_range(w3, current_start, current_end)

            # Check if df is None or empty, and if so, return early without further processing.
            if df is None or df.empty:
                print(f"Skipping blocks {current_start} to {current_end} due to no data.")
                return

            save_data_for_range(df, current_start, current_end, chain, s3_connection, bucket_name)
            
            if chain == 'linea':
                df_prep = prep_dataframe_linea(df)
            elif chain == 'scroll':
                df_prep = prep_dataframe_scroll(df)
            else:
                df_prep = prep_dataframe(df)

            df_prep.drop_duplicates(subset=['tx_hash'], inplace=True)
            df_prep.set_index('tx_hash', inplace=True)
            df_prep.index.name = 'tx_hash'
            
            try:
                db_connector.upsert_table(table_name, df_prep, if_exists='update')  # Use DbConnector for upserting data
                print(f"Data inserted for blocks {current_start} to {current_end} successfully.")
            except Exception as e:
                print(f"Error inserting data for blocks {current_start} to {current_end}: {e}")
                raise e
            break  # Break out of the loop on successful execution

        except Exception as e:
            print(f"Error processing blocks {current_start} to {current_end}: {e}")
            base_wait_time = handle_retry_exception(current_start, current_end, base_wait_time)
