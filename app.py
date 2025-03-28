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

# โหลด environment variables
load_dotenv()

app = Flask(__name__)

# การตั้งค่า Google Drive API จาก environment variables
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
ROOT_FOLDER_ID = os.getenv('ROOT_FOLDER_ID', '12ZSx-p7Y-0dWp1rmVbMm5Rnh4AVEnS-A')
CENTRAL_FOLDER_ID = os.getenv('CENTRAL_FOLDER_ID', '1Mvanpcj2-wsd2eeObHMthqVHjmqH-wCx')

def get_drive_service():
    try:
        # อ่าน credentials จากไฟล์ที่ระบุใน GOOGLE_CREDENTIALS_PATH
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=credentials)
        print("Successfully connected to Google Drive API")
        return service
    except Exception as e:
        print(f"Error connecting to Google Drive API: {str(e)}")
        raise

# ดึงข้อมูลจาก Google Drive
def get_employee_data(service):
    print(f"Fetching folders from root folder ID: {ROOT_FOLDER_ID}")
    try:
        results = service.files().list(
            q=f"'{ROOT_FOLDER_ID}' in parents",
            fields="files(id, name)"
        ).execute()
    except Exception as e:
        print(f"Error fetching folders: {str(e)}")
        raise
    
    folders = results.get('files', [])
    print(f"Found folders: {folders}")
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
        except Exception as e:
            print(f"Error fetching files for {employee_name}: {str(e)}")
            continue
        
        employee_files = files.get('files', [])
        print(f"Found files for {employee_name}: {employee_files}")
        
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

# อัปโหลดไฟล์รวมไปยัง Google Drive (สร้างใหม่ทุกครั้ง)
def upload_combined_file(service, df):
    combined_file = 'combined_timesheet.csv'
    
    try:
        with open(combined_file, 'w', encoding='utf-8') as f:
            df.to_csv(f, index=False)
        
        try:
            existing_files = service.files().list(
                q=f"'{CENTRAL_FOLDER_ID}' in parents and name='combined_timesheet.csv'",
                fields="files(id, name)"
            ).execute().get('files', [])
        except Exception as e:
            print(f"Error checking existing files in Central folder: {str(e)}")
            raise
        
        if existing_files:
            for file in existing_files:
                print(f"Deleting existing file: {file['name']} (ID: {file['id']})")
                try:
                    service.files().delete(fileId=file['id']).execute()
                except Exception as e:
                    print(f"Error deleting file {file['name']}: {str(e)}")
                    raise
        
        print("Creating new combined_timesheet.csv")
        try:
            file_metadata = {
                'name': 'combined_timesheet.csv',
                'parents': [CENTRAL_FOLDER_ID]
            }
            media = MediaFileUpload(combined_file, mimetype='text/csv')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        except Exception as e:
            print(f"Error creating new combined_timesheet.csv: {str(e)}")
            raise
    finally:
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                if os.path.exists(combined_file):
                    os.remove(combined_file)
                    print(f"Successfully deleted temporary file: {combined_file}")
                break
            except Exception as e:
                print(f"Attempt {attempt + 1}/{max_attempts} - Error deleting temporary file: {str(e)}")
                if attempt < max_attempts - 1:
                    time.sleep(1)

# สรุปข้อมูล
def summarize_data(df):
    if df.empty:
        return pd.DataFrame()
    
    # เปลี่ยนรูปแบบวันที่ให้อ่านง่าย (เช่น จาก "2025-03-17" เป็น "17 Mar 2025")
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%d %b %Y')
    
    # สรุปข้อมูลโดยใช้ groupby และ unstack
    summary = df.groupby(['Employee', 'Date', 'Status']).size().unstack(fill_value=0)
    
    # ตรวจสอบและเพิ่มคอลัมน์ที่จำเป็นถ้าขาด
    required_columns = ['Late', 'Leave', 'WFH', 'WFO']
    for col in required_columns:
        if col not in summary.columns:
            summary[col] = 0
    
    # เรียงลำดับคอลัมน์ให้แน่นอน
    summary = summary[required_columns]
    
    # รีเซ็ต index เพื่อให้ Employee และ Date กลายเป็นคอลัมน์ปกติ
    summary = summary.reset_index()
    
    # Debug: แสดงชื่อคอลัมน์ที่ได้
    print("Summary columns:", summary.columns.tolist())
    print("Summary data:\n", summary)
    
    return summary

@app.route('/', methods=['GET'])
def dashboard():
    try:
        service = get_drive_service()
        raw_data = get_employee_data(service)
        message = "No data available"
        
        # รับค่าจากฟอร์ม (กรองตามชื่อพนักงาน)
        employee_filter = request.args.get('employee', default=None)
        
        if not raw_data.empty:
            # กรองข้อมูลตามชื่อพนักงาน (ถ้ามี)
            filtered_data = raw_data
            if employee_filter:
                filtered_data = raw_data[raw_data['Employee'].str.contains(employee_filter, case=False, na=False)]
            
            # อัปโหลดข้อมูลที่กรองแล้ว
            upload_combined_file(service, filtered_data)
            summary = summarize_data(filtered_data)
            
            # ตรวจสอบให้แน่ใจว่าเรียงคอลัมน์ถูกต้อง
            summary = summary[['Employee', 'Date', 'Late', 'Leave', 'WFH', 'WFO']]
            table_html = summary.to_html(classes='table table-striped table-bordered table-hover', index=False)
            message = "Data updated successfully"
            
            # บันทึก summary ลงในไฟล์เพื่อใช้ในการดาวน์โหลด
            summary.to_csv('summary_for_download.csv', index=False)
        else:
            table_html = "<p>No data available</p>"
    except Exception as e:
        table_html = f"<p>Error: {str(e)}</p>"
        message = f"Error: {str(e)}"
        print(f"Error in dashboard: {str(e)}")
    
    return render_template('dashboard.html', table=table_html, date=datetime.now().strftime('%Y-%m-%d'), message=message)

@app.route('/download', methods=['GET'])
def download_csv():
    try:
        # อ่านไฟล์ summary ที่บันทึกไว้
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
        # ลบไฟล์หลังจากดาวน์โหลด
        if os.path.exists('summary_for_download.csv'):
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    os.remove('summary_for_download.csv')
                    print(f"Successfully deleted temporary summary file: summary_for_download.csv")
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1}/{max_attempts} - Error deleting temporary summary file: {str(e)}")
                    if attempt < max_attempts - 1:
                        time.sleep(1)

if __name__ == '__main__':
    # สำหรับ development
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
else:
    # สำหรับ production (เช่น บน Render)
    import gunicorn