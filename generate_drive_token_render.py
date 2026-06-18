# generate_drive_token_render.py
import pickle
import base64
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes for Drive upload
SCOPES = ['https://www.googleapis.com/auth/drive']

print("🔐 Generating Google Drive token for Render...")
print("📁 This will allow creating folders and uploading files")

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=0)

# Generate base64 for Render
token_json = json.dumps({
    'token': creds.token,
    'refresh_token': creds.refresh_token,
    'token_uri': creds.token_uri,
    'client_id': creds.client_id,
    'client_secret': creds.client_secret,
    'scopes': creds.scopes
})
encoded = base64.b64encode(token_json.encode()).decode()

print("\n" + "=" * 60)
print("📌 GOOGLE_DRIVE_TOKEN (copy this to Render):")
print("=" * 60)
print(encoded)
print("=" * 60)