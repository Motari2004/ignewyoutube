import os
import asyncio
import logging
import sys
from urllib.parse import quote
from quart import Quart, render_template, request, Response
import yt_dlp
import httpx
import pickle
import json
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import tempfile
import time

# -----------------------------
# Configuration & Environment
# -----------------------------
PORT = int(os.environ.get("PORT", 5000))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://igdown-01en.onrender.com/health")
CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB
COOKIE_FILE = "instagram_cookies.txt"

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive']

# Instagram Downloads Folder Name in Google Drive
INSTAGRAM_FOLDER_NAME = "Instagram_Downloads"

# Handle Cookies from Environment
COOKIES_CONTENT = os.environ.get("COOKIES_CONTENT")
if COOKIES_CONTENT:
    with open(COOKIE_FILE, "w") as f:
        f.write(COOKIES_CONTENT)

app = Quart(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("IG-Drive-DL")

# -----------------------------
# Google Drive Authentication
# -----------------------------
class GoogleDriveUploader:
    def __init__(self):
        self.drive_service = self.authenticate_drive()
        self.instagram_folder_id = self.get_or_create_folder()
        self.is_render = os.environ.get('RENDER') == 'true'
    
    def authenticate_drive(self):
        """Authenticate with Google Drive API"""
        creds = None
        
        # 1. Try environment variable (Render deployment)
        token_json = os.environ.get('GOOGLE_DRIVE_TOKEN')
        if token_json:
            try:
                token_data = json.loads(base64.b64decode(token_json).decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logger.info("✅ Authenticated using GOOGLE_DRIVE_TOKEN from env")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"⚠️ Failed to use GOOGLE_DRIVE_TOKEN: {e}")
        
        # 2. Try local token file (Desktop/Local)
        if os.path.exists('token.pickle'):
            try:
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
                logger.info("✅ Authenticated using token.pickle")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"⚠️ Failed to load token.pickle: {e}")
        
        # 3. Try credentials.json for interactive auth (Desktop/Local)
        if os.path.exists('credentials.json'):
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("✅ Authenticated using credentials.json")
                
                # Save token for future use
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
                logger.info("💾 Token saved to token.pickle for future use")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"⚠️ Failed to authenticate with credentials.json: {e}")
        
        # 4. Try environment credentials (Render)
        credentials_json = os.environ.get('GOOGLE_CREDENTIALS')
        if credentials_json:
            try:
                credentials_data = json.loads(base64.b64decode(credentials_json).decode('utf-8'))
                if 'client_email' in credentials_data:  # Service account
                    from google.oauth2 import service_account
                    creds = service_account.Credentials.from_service_account_info(
                        credentials_data, scopes=SCOPES
                    )
                    logger.info("✅ Authenticated using service account from env")
                    return build('drive', 'v3', credentials=creds)
                else:  # OAuth2
                    flow = InstalledAppFlow.from_client_config(credentials_data, SCOPES)
                    creds = flow.run_local_server(port=0, open_browser=False)
                    logger.info("✅ Authenticated using OAuth2 from env")
                    return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"⚠️ Failed to authenticate with GOOGLE_CREDENTIALS: {e}")
        
        # 5. If nothing works, log error but don't crash
        logger.error("❌ No valid credentials found for Google Drive!")
        logger.info("📌 Continuing without Drive upload...")
        return None
    
    def get_or_create_folder(self):
        """Get or create the Instagram Downloads folder in Google Drive"""
        if not self.drive_service:
            return None
        
        try:
            # Search for existing folder
            query = f"name='{INSTAGRAM_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            
            if files:
                folder_id = files[0]['id']
                logger.info(f"📁 Found existing folder: {INSTAGRAM_FOLDER_NAME} (ID: {folder_id})")
                return folder_id
            else:
                # Create new folder
                file_metadata = {
                    'name': INSTAGRAM_FOLDER_NAME,
                    'mimeType': 'application/vnd.google-apps.folder'
                }
                folder = self.drive_service.files().create(body=file_metadata, fields='id').execute()
                folder_id = folder.get('id')
                logger.info(f"📁 Created new folder: {INSTAGRAM_FOLDER_NAME} (ID: {folder_id})")
                return folder_id
                
        except Exception as e:
            logger.error(f"❌ Error creating/finding folder: {e}")
            return None
    
    async def upload_to_drive(self, file_path, folder_id=None):
        """Upload file to Google Drive - uses Instagram folder by default"""
        if not self.drive_service:
            logger.error("❌ No Drive service available")
            return None
        
        if not file_path or not os.path.exists(file_path):
            logger.error("❌ File not found")
            return None
        
        try:
            file_name = os.path.basename(file_path)
            logger.info(f"📤 Uploading: {file_name} to Google Drive...")
            
            # Use Instagram folder if no folder_id provided
            target_folder = folder_id if folder_id else self.instagram_folder_id
            
            file_metadata = {
                'name': file_name
            }
            
            if target_folder:
                file_metadata['parents'] = [target_folder]
            
            media = MediaFileUpload(
                file_path,
                mimetype='video/mp4',
                resumable=True
            )
            
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink, parents'
            ).execute()
            
            logger.info(f"✅ Upload complete: {file.get('name')}")
            logger.info(f"🔗 Link: {file.get('webViewLink')}")
            logger.info(f"📁 Folder: {INSTAGRAM_FOLDER_NAME}")
            
            return file
            
        except Exception as e:
            logger.error(f"❌ Upload error: {str(e)}")
            return None

# -----------------------------
# Anti-Sleep Engine
# -----------------------------
async def keep_alive():
    """Background task to ping the public URL every 10 minutes."""
    await asyncio.sleep(20)
    logger.info(f"🚀 Anti-Sleep Engine targeting: {PUBLIC_URL}")
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        while True:
            try:
                response = await client.get(PUBLIC_URL, timeout=30.0)
                logger.info(f"❤️ HEARTBEAT SUCCESS: [Status {response.status_code}] Engine Active")
            except Exception as e:
                logger.warning(f"💔 HEARTBEAT DELAYED: {e}")
            await asyncio.sleep(600)

@app.before_serving
async def startup_tasks():
    app.add_background_task(keep_alive)

# -----------------------------
# Routes
# -----------------------------
@app.route("/health")
async def health():
    """Endpoint for keep-alive pings."""
    return {"status": "optimized", "engine": "ig-drive-v2.0", "state": "online"}, 200

@app.route("/")
async def index():
    return await render_template("index.html")

# -----------------------------
# Instagram Download Logic
# -----------------------------
async def extract_instagram_info(url: str) -> dict:
    ydl_opts = {
        "format": "best",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": COOKIE_FILE if os.path.exists(COOKIE_FILE) else None,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    def extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise ValueError("Unable to extract video information")
            return info

    info = await asyncio.to_thread(extract)
    
    # Get the downloaded file path
    filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
    
    # Clean filename
    raw_title = info.get('title', 'instagram_video')
    clean_filename = "".join([c for c in raw_title if c.isalnum() or c in (' ', '_')]).strip()
    
    return {
        "video_url": info.get("url", ""),
        "headers": info.get("http_headers", {}),
        "filename": filename,
        "clean_title": clean_filename,
        "title": info.get("title", "instagram_video")
    }

async def range_stream(video_url: str, base_headers: dict):
    timeout = httpx.Timeout(connect=20.0, read=None, write=20.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            head = await client.head(video_url, headers=base_headers)
            total_size = int(head.headers.get("Content-Length", 0))
        except Exception:
            total_size = 0 

        start = 0
        while True:
            end = start + CHUNK_SIZE - 1
            if total_size and end >= total_size: end = total_size - 1
            headers = {**base_headers, "Range": f"bytes={start}-{end}", "Connection": "keep-alive"}

            try:
                async with client.stream("GET", video_url, headers=headers) as r:
                    if r.status_code == 416: break
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes():
                        if chunk: yield chunk
            except Exception:
                break

            if total_size and end >= total_size - 1: break
            start = end + 1
            await asyncio.sleep(0.01)

@app.route("/download", methods=["POST"])
async def download():
    form = await request.form
    url = form.get("url")
    folder_id = form.get("folder_id", "")
    
    if not url:
        return "❌ No URL provided", 400
    
    try:
        # Step 1: Extract and download the video
        logger.info(f"📥 Downloading: {url}")
        info = await extract_instagram_info(url)
        
        # Step 2: Upload to Google Drive (auto-uses Instagram folder)
        drive = GoogleDriveUploader()
        file = await drive.upload_to_drive(info["filename"], folder_id)
        
        # Step 3: Clean up local file
        try:
            if os.path.exists(info["filename"]):
                os.remove(info["filename"])
                logger.info(f"🗑️ Deleted local file: {info['filename']}")
        except Exception as e:
            logger.warning(f"⚠️ Cleanup error: {e}")
        
        if file:
            # Return the Drive link
            return {
                "success": True,
                "message": f"✅ Upload complete: {file.get('name')}",
                "link": file.get('webViewLink'),
                "file_id": file.get('id'),
                "folder": INSTAGRAM_FOLDER_NAME
            }, 200
        else:
            return {
                "success": False,
                "message": "❌ Video downloaded but upload to Drive failed. Check Drive authentication."
            }, 500
            
    except Exception as e:
        logger.exception("Extraction failed")
        return {
            "success": False,
            "message": f"❌ Error: {str(e)}"
        }, 500

# -----------------------------
# HTML Template
# -----------------------------
@app.route("/template")
async def get_template():
    return await render_template_string(HTML_TEMPLATE)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Instagram to Google Drive Downloader</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 { color: #333; text-align: center; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; color: #555; }
        input, select {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            box-sizing: border-box;
        }
        button {
            background: #007bff;
            color: white;
            padding: 12px 30px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
        }
        button:hover { background: #0056b3; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        .status {
            margin-top: 20px;
            padding: 15px;
            border-radius: 5px;
            display: none;
        }
        .status.active { display: block; }
        .status.loading { background: #e3f2fd; border: 1px solid #2196f3; }
        .status.success { background: #e8f5e9; border: 1px solid #4caf50; }
        .status.error { background: #ffebee; border: 1px solid #f44336; }
        .link { color: #007bff; text-decoration: none; }
        .link:hover { text-decoration: underline; }
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #007bff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .progress-bar {
            width: 100%;
            background: #f0f0f0;
            border-radius: 5px;
            margin: 10px 0;
            overflow: hidden;
        }
        .progress-fill {
            height: 20px;
            background: #007bff;
            transition: width 0.5s;
            width: 0%;
        }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            color: white;
            margin-left: 10px;
            background: #28a745;
        }
        .folder-info {
            background: #e3f2fd;
            border: 1px solid #2196f3;
            color: #0d47a1;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 15px;
            font-size: 14px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📸 Instagram to Google Drive
            <span class="badge">Auto-Upload</span>
        </h1>
        <p style="text-align:center;color:#666;">Download Instagram Reels & Videos directly to Google Drive</p>
        
        <div class="folder-info">
            📁 Videos will be saved to: <strong>Instagram_Downloads</strong> folder
        </div>
        
        <form id="downloadForm">
            <div class="form-group">
                <label for="url">Instagram URL</label>
                <input type="text" id="url" name="url" placeholder="https://www.instagram.com/reel/..." required>
            </div>
            
            <div class="form-group">
                <label for="folder_id">Google Drive Folder ID (Optional)</label>
                <input type="text" id="folder_id" name="folder_id" placeholder="Leave empty for Instagram_Downloads folder">
            </div>
            
            <button type="submit" id="submitBtn">Download & Upload to Drive</button>
        </form>
        
        <div id="status" class="status">
            <div id="statusContent"></div>
        </div>
        
        <div id="progressContainer" style="display: none;">
            <div class="progress-bar">
                <div id="progressFill" class="progress-fill"></div>
            </div>
            <div id="progressText">0%</div>
        </div>
    </div>

    <script>
        document.getElementById('downloadForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const statusDiv = document.getElementById('status');
            const statusContent = document.getElementById('statusContent');
            const progressContainer = document.getElementById('progressContainer');
            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');
            
            statusDiv.className = 'status';
            statusDiv.style.display = 'none';
            progressContainer.style.display = 'none';
            submitBtn.disabled = true;
            submitBtn.textContent = 'Processing...';
            
            const formData = new FormData(this);
            
            try {
                const response = await fetch('/download', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    statusDiv.className = 'status active success';
                    statusDiv.style.display = 'block';
                    statusContent.innerHTML = '✅ ' + data.message + 
                        '<br>📁 Folder: <strong>' + data.folder + '</strong>' +
                        '<br>🔗 <a href="' + data.link + '" target="_blank" class="link">View in Google Drive</a>';
                } else {
                    statusDiv.className = 'status active error';
                    statusDiv.style.display = 'block';
                    statusContent.innerHTML = data.message;
                }
                
            } catch (error) {
                statusDiv.className = 'status active error';
                statusDiv.style.display = 'block';
                statusContent.innerHTML = '❌ Error: ' + error.message;
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Download & Upload to Drive';
            }
        });
    </script>
</body>
</html>
"""

# -----------------------------
# Template Helper
# -----------------------------
from quart import render_template_string

# -----------------------------
# Deployment Setup
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)