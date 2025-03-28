from flask import Flask, render_template, request, send_file
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import pandas as pd
import io
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from googleapiclient.errors import HttpError

# โหลด environment variables
load_dotenv()

app = Flask(__name__)

# การตั้งค่า Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
ROOT_FOLDER_ID = os.getenv('ROOT_FOLDER_ID', '12ZSx-p7Y-0dWp1rmVbMm5Rnh4AVEnS-A')
CENTRAL_FOLDER_ID = os.getenv('CENTRAL_FOLDER_ID', '1Mvanpcj2-wsd2eeObHMthqVHjmqH-wCx')

def get_drive_service():
    print(f"Attempting to load credentials from: {SERVICE_ACCOUNT_FILE}")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"Credentials file not found at: {SERVICE_ACCOUNT_FILE}")
        raise FileNotFoundError(f"Credentials file not found: {SERVICE_ACCOUNT_FILE}")
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=credentials)
        print("Successfully connected to Google Drive API")
        return service
    except Exception as e:
        print(f"Error connecting to Google Drive API: {str(e)}")
        raise

def get_employee_data(service):
    print(f"Fetching folders from root folder ID: {ROOT_FOLDER_ID}")
    try:
        results = service.files().list(
            q=f"'{ROOT_FOLDER_ID}' in parents",
            fields="files(id, name)"
        ).execute()
        folders = results.get('files', [])
        print(f"Found folders: {folders}")
    except Exception as e:
        print(f"Error fetching folders: {str(e)}")
        raise
    
    all_data = []
    for folder in folders:
        if folder['name'] == 'Central':
            continue
        employee_name = folder['name']
        print(f"Processing employee: {employee_name}")
        try:
            files = service.files().list(
                q=f"'{folder['id']}' in parents and mimeType='text/csv'",
                fields="files(id, name)"
            ).execute()
            employee_files = files.get('files', [])
            print(f"Found files for {employee_name}: {employee_files}")
        except Exception as e:
            print(f"Error fetching files for {employee_name}: {str(e)}")
            continue
        
        for file in employee_files:
            print(f"Downloading file: {file['name']}")
            try:
                request = service.files().get_media(fileId=file['id'])
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                fh.seek(0)
                df = pd.read_csv(fh)
                print(f"Data for {employee_name}: {df}")
                df['Employee'] = employee_name
                all_data.append(df)
            except Exception as e:
                print(f"Error processing file {file['name']} for {employee_name}: {str(e)}")
                continue

    combined_data = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    print(f"Combined data: {combined_data}")
    return combined_data

def upload_combined_file(service, df):
    combined_file = 'combined_timesheet.csv'
    success = False
    
    try:
        with open(combined_file, 'w', encoding='utf-8') as f:
            df.to_csv(f, index=False)
        print(f"Created temporary file: {combined_file}")
        
        try:
            existing_files = service.files().list(
                q=f"'{CENTRAL_FOLDER_ID}' in parents and name='combined_timesheet.csv'",
                fields="files(id, name)"
            ).execute().get('files', [])
            print(f"Existing files in Central folder: {existing_files}")
        except Exception as e:
            print(f"Error checking existing files: {str(e)}")
            return False
        
        if existing_files:
            for file in existing_files:
                print(f"Attempting to delete file: {file['name']} (ID: {file['id']})")
                try:
                    service.files().delete(fileId=file['id']).execute()
                    print(f"Deleted file: {file['name']}")
                except HttpError as e:
                    if e.resp.status == 404:
                        print(f"File {file['name']} not found, skipping: {str(e)}")
                    else:
                        print(f"Error deleting file {file['name']}: {str(e)}")
                        return False
                except Exception as e:
                    print(f"Unexpected error deleting file {file['name']}: {str(e)}")
                    return False
        
        print("Uploading new combined_timesheet.csv")
        try:
            file_metadata = {
                'name': 'combined_timesheet.csv',
                'parents': [CENTRAL_FOLDER_ID]
            }
            media = MediaFileUpload(combined_file, mimetype='text/csv')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print("Successfully uploaded combined_timesheet.csv")
            success = True
        except Exception as e:
            print(f"Error uploading combined_timesheet.csv: {str(e)}")
            return False
    except Exception as e:
        print(f"Error in upload_combined_file: {str(e)}")
        return False
    finally:
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                if os.path.exists(combined_file):
                    os.remove(combined_file)
                    print(f"Deleted temporary file: {combined_file}")
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} to delete temp file failed: {str(e)}")
                if attempt < max_attempts - 1:
                    time.sleep(1)
    return success

def summarize_data(df):
    if df.empty:
        print("No data to summarize")
        return pd.DataFrame()
    
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%d %b %Y')
    summary = df.groupby(['Employee', 'Date', 'Status']).size().unstack(fill_value=0)
    required_columns = ['Late', 'Leave', 'WFH', 'WFO']
    for col in required_columns:
        if col not in summary.columns:
            summary[col] = 0
    summary = summary[required_columns]
    summary = summary.reset_index()
    print("Summary columns:", summary.columns.tolist())
    print("Summary data:\n", summary)
    return summary

@app.route('/', methods=['GET'])
def dashboard():
    message = "No data available"
    table_html = "<p>No data available</p>"
    try:
        service = get_drive_service()
        raw_data = get_employee_data(service)
        
        employee_filter = request.args.get('employee', default=None)
        
        if not raw_data.empty:
            filtered_data = raw_data
            if employee_filter:
                filtered_data = raw_data[raw_data['Employee'].str.contains(employee_filter, case=False, na=False)]
            
            upload_success = upload_combined_file(service, filtered_data)
            summary = summarize_data(filtered_data)
            if not summary.empty:
                summary = summary[['Employee', 'Date', 'Late', 'Leave', 'WFH', 'WFO']]
                table_html = summary.to_html(classes='table table-striped table-bordered table-hover', index=False)
                message = "Data updated successfully" if upload_success else "Data processed but upload failed"
                summary.to_csv('summary_for_download.csv', index=False)
    except Exception as e:
        table_html = f"<p>Error: {str(e)}</p>"
        message = f"Error: {str(e)}"
        print(f"Error in dashboard: {str(e)}")
    
    return render_template('dashboard.html', table=table_html, date=datetime.now().strftime('%Y-%m-%d'), message=message)

@app.route('/download', methods=['GET'])
def download_csv():
    try:
        if os.path.exists('summary_for_download.csv'):
            return send_file(
                'summary_for_download.csv',
                as_attachment=True,
                download_name='timesheet_summary.csv',
                mimetype='text/csv'
            )
        else:
            return "Error: No data available to download", 404
    except Exception as e:
        print(f"Error in download_csv: {str(e)}")
        return f"Error: {str(e)}", 500
    finally:
        if os.path.exists('summary_for_download.csv'):
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    os.remove('summary_for_download.csv')
                    print(f"Deleted temporary summary file")
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1} to delete summary file failed: {str(e)}")
                    if attempt < max_attempts - 1:
                        time.sleep(1)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"Starting Flask on port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
else:
    import gunicorn
    port = int(os.getenv('PORT', 5000))
    print(f"Gunicorn detected, binding to port {port}")