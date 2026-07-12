"""
One-off helper to generate the support@ Google Drive OAuth2 token.pickle for the
WhatsApp chat import.

WHY A SEPARATE TOKEN: the WhatsApp export folder is shared to
support@alter-power.co.za, a DIFFERENT Google account from the info@ one used by
the DB-backup task. The account you SIGN IN AS in the browser is whose token you
get — so sign in as support@.

    >>> This is NOT part of the app runtime. Run it once, locally, on a machine
        with a browser. Then copy the resulting .pickle to the server and point
        WHATSAPP_TOKEN_PICKLE_PATH at it. <<<

PREREQUISITES (local machine):
    pip install google-auth-oauthlib google-api-python-client
    A client-secrets JSON downloaded from Google Cloud Console
    (APIs & Services > Credentials > OAuth client ID, type "Desktop app").

USAGE:
    python -m apps.whatsapp_import.generate_support_token \
        --client-secrets /path/to/client_secret.json \
        --out whatsapp_support_token.pickle

    A browser opens. Log in as support@alter-power.co.za and click Allow.
    The pickle is written to --out.

VERIFY it can see the shared folder:
    python -m apps.whatsapp_import.generate_support_token \
        --out whatsapp_support_token.pickle --list-folder <WHATSAPP_DRIVE_FOLDER_ID>
"""

import argparse
import pickle
import sys

# Read-only Drive scope is all the import needs.
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


def generate(client_secrets_path, out_path):
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
    # Opens a browser; whichever Google account you sign in as owns the token.
    print("A browser window will open. Sign in as support@alter-power.co.za and click Allow.")
    creds = flow.run_local_server(port=0)

    with open(out_path, 'wb') as fh:
        pickle.dump(creds, fh)
    print(f"\nSaved token to: {out_path}")
    print("Copy this file to the server and set WHATSAPP_TOKEN_PICKLE_PATH to its path.")


def list_folder(out_path, folder_id):
    """Sanity check: list the shared folder using the generated token."""
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    with open(out_path, 'rb') as fh:
        creds = pickle.load(fh)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    resp = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields='files(id, name, mimeType, modifiedTime)',
        pageSize=50,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get('files', [])
    print(f"{len(files)} item(s) in folder {folder_id}:")
    for f in files:
        print(f"  - {f['name']}  [{f['mimeType']}]  (modified {f.get('modifiedTime')})")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate/verify the support@ Drive token for WhatsApp import.")
    parser.add_argument('--client-secrets', help="Path to the OAuth client-secrets JSON (for generating).")
    parser.add_argument('--out', default='whatsapp_support_token.pickle', help="Where to write / read the pickle.")
    parser.add_argument('--list-folder', metavar='FOLDER_ID',
                        help="Skip generation; instead list this folder using --out's pickle (verify access).")
    args = parser.parse_args(argv)

    if args.list_folder:
        list_folder(args.out, args.list_folder)
        return 0

    if not args.client_secrets:
        parser.error("--client-secrets is required to generate a token "
                     "(or pass --list-folder to verify an existing one).")
    generate(args.client_secrets, args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
