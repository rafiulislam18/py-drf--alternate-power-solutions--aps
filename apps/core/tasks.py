import os
import pickle
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from celery import shared_task
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


@shared_task(bind=True, max_retries=3)
def daily_postgres_backup(self):
    """
    Daily PostgreSQL backup to Google Drive at 00:00 SAST (Cape Town time).
    Keeps only the last 3 backup files in the folder.
    Uses OAuth2 refresh token stored in token.pickle (no service account key needed).
    """
    try:
        # =============================================
        # 1. Create the backup file (pg_dump + gzip)
        # =============================================
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup_filename = f"aps_db_backup_{timestamp}.sql.gz"

        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = Path(tmpdir) / backup_filename

            # pg_dump + gzip
            env = os.environ.copy()
            env['PGPASSWORD'] = os.getenv('DB_PASSWORD')

            cmd = [
                'pg_dump',
                '-h', os.getenv('DB_HOST', 'localhost'),
                '-U', os.getenv('DB_USER'),
                '-d', os.getenv('DB_NAME'),
                '--clean', '--if-exists',
            ]

            with open(backup_path, 'wb') as f:
                dump_proc = subprocess.run(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    check=True
                )
                gzip_proc = subprocess.run(
                    ['gzip', '-c'],
                    input=dump_proc.stdout,
                    stdout=f,
                    check=True
                )

            print(f"Backup created: {backup_filename} ({backup_path.stat().st_size / (1024 * 1024):.2f} MB)")

            # =============================================
            # 2. Load OAuth2 credentials from token.pickle
            # =============================================
            token_path = os.getenv('TOKEN_PICKLE_PATH')

            creds = None
            if os.path.exists(token_path):
                with open(token_path, 'rb') as token_file:
                    creds = pickle.load(token_file)

            # Refresh if expired (using the stored refresh token)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())

            if not creds or not creds.valid:
                raise ValueError(
                    "Invalid or missing Google Drive credentials. "
                    "Re-run the token authorization script on your local machine."
                )

            # Build Drive service
            service = build('drive', 'v3', credentials=creds)

            # =============================================
            # 3. Upload the backup to Google Drive
            # =============================================
            folder_id = os.getenv('DRIVE_BACKUP_FOLDER_ID')
            if not folder_id:
                raise ValueError("DRIVE_BACKUP_FOLDER_ID not set in .env")

            file_metadata = {
                'name': backup_filename,
                'parents': [folder_id],
            }

            media = MediaFileUpload(
                backup_path,
                mimetype='application/gzip',
                resumable=True
            )

            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            print(f"Uploaded to Google Drive → File ID: {file.get('id')}")

            # =============================================
            # 4. Retention: keep only the last 3 backups
            # =============================================
            query = (
                f"'{folder_id}' in parents "
                "and name contains 'aps_db_backup_' "
                "and mimeType = 'application/gzip'"
            )
            
            results = service.files().list(
                q=query,
                orderBy='name desc',           # newest first (YYYY-MM-DD_HH-MM-SS)
                fields='files(id, name, createdTime)',
                pageSize=20                    # safety margin
            ).execute()

            files = results.get('files', [])

            if len(files) > 3:
                to_delete = files[3:]  # keep the first 3 (newest)
                for old_file in to_delete:
                    service.files().delete(fileId=old_file['id']).execute()
                    print(f"Deleted old backup: {old_file['name']}")
                print(f"Retention applied: kept {len(files) - len(to_delete)}, deleted {len(to_delete)}")
            else:
                print(f"Only {len(files)} backups found — retention not needed yet")

        return f"Backup successful: {backup_filename} (ID: {file.get('id')})"

    except Exception as exc:
        print(f"Backup task failed: {exc}")
        raise self.retry(exc=exc, countdown=300)  # retry in 5 minutes
