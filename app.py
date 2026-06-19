"""
Intelligent Daily Reel Downloader - Loads URLs from Google Drive & Reel Finder
"""

import os
import asyncio
import logging
import sys
import json
import re
import base64
import pickle
import io
from datetime import datetime
from typing import List, Dict, Optional

from quart import Quart, request, jsonify, render_template_string
import yt_dlp
import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# -----------------------------
# Configuration
# -----------------------------
PORT = int(os.environ.get("PORT", 8080))

def get_reel_finder_url():
    if os.environ.get("REEL_FINDER_URL"):
        return os.environ.get("REEL_FINDER_URL")
    if os.environ.get("RENDER"):
        service_name = os.environ.get("RENDER_SERVICE_NAME", "daily-reel-generator")
        return f"https://{service_name}.onrender.com"
    return "http://localhost:5000"

REEL_FINDER_URL = get_reel_finder_url()
COOKIE_FILE = "instagram_cookies.txt"

# Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive']
INSTAGRAM_FOLDER_NAME = "Instagram_Daily_Reels"
DOWNLOADED_LOG_FILE = "downloaded_reels.json"
PENDING_LOG_FILE = "pending_reels.json"
FOUND_URLS_FILE = "found_urls.json"
SHARED_DRIVE_FOLDER = "Reel_Finder_Data"
SHARED_FILE_NAME = "shared_reels.json"

COOKIES_CONTENT = os.environ.get("COOKIES_CONTENT")
if COOKIES_CONTENT:
    with open(COOKIE_FILE, "w") as f:
        f.write(COOKIES_CONTENT)

app = Quart(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("Reel-Downloader")
logger.info(f"📌 Using Reel Finder at: {REEL_FINDER_URL}")












class SharedDriveLoader:
    """Load shared data from Google Drive"""
    
    def __init__(self):
        self.service = self._authenticate()
        self.folder_id = self._get_folder_id()
    
    def _authenticate(self):
        creds = None
        
        # 1. Try environment token (Render)
        token_json = os.environ.get('GOOGLE_DRIVE_TOKEN')
        if token_json:
            try:
                token_data = json.loads(base64.b64decode(token_json).decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logger.info("Drive authenticated via GOOGLE_DRIVE_TOKEN")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"GOOGLE_DRIVE_TOKEN failed: {e}")
        
        # 2. Try local token file
        if os.path.exists('drive_token.pickle'):
            try:
                with open('drive_token.pickle', 'rb') as f:
                    creds = pickle.load(f)
                logger.info("Drive authenticated via drive_token.pickle")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"drive_token.pickle failed: {e}")
        
        # 3. Try credentials.json (Local development)
        if os.path.exists('credentials.json'):
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("Drive authenticated via credentials.json")
                # Save token for future use
                with open('drive_token.pickle', 'wb') as f:
                    pickle.dump(creds, f)
                logger.info("Token saved to drive_token.pickle for future use")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"credentials.json authentication failed: {e}")
                # Try alternative method if run_local_server fails
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_console()
                    logger.info("Drive authenticated via credentials.json (console mode)")
                    with open('drive_token.pickle', 'wb') as f:
                        pickle.dump(creds, f)
                    logger.info("Token saved to drive_token.pickle for future use")
                    return build('drive', 'v3', credentials=creds)
                except Exception as e2:
                    logger.warning(f"credentials.json console auth failed: {e2}")
        
        # 4. Try service account (Render)
        credentials_json = os.environ.get('GOOGLE_CREDENTIALS')
        if credentials_json:
            try:
                credentials_data = json.loads(base64.b64decode(credentials_json).decode('utf-8'))
                if 'client_email' in credentials_data:
                    from google.oauth2 import service_account
                    creds = service_account.Credentials.from_service_account_info(
                        credentials_data, scopes=SCOPES
                    )
                    logger.info("Drive authenticated via service account")
                    return build('drive', 'v3', credentials=creds)
                else:
                    # Try OAuth2 flow with client config
                    from google_auth_oauthlib.flow import InstalledAppFlow
                    flow = InstalledAppFlow.from_client_config(credentials_data, SCOPES)
                    creds = flow.run_local_server(port=0, open_browser=False)
                    logger.info("Drive authenticated via GOOGLE_CREDENTIALS OAuth2")
                    return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"GOOGLE_CREDENTIALS failed: {e}")
        
        # 5. Try to create credentials from environment as fallback
        creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        if creds_json:
            try:
                creds_data = json.loads(base64.b64decode(creds_json).decode('utf-8'))
                flow = InstalledAppFlow.from_client_config(creds_data, SCOPES)
                creds = flow.run_local_server(port=0, open_browser=False)
                logger.info("Drive authenticated via GOOGLE_CREDENTIALS_JSON")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"GOOGLE_CREDENTIALS_JSON failed: {e}")
        
        logger.error("❌ No Drive credentials found")
        logger.info("📌 To authenticate, place 'credentials.json' in the project folder")
        logger.info("📌 Or set GOOGLE_DRIVE_TOKEN environment variable")
        return None
    
    def _get_folder_id(self):
        if not self.service:
            return None
        
        try:
            query = f"name='{SHARED_DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            if files:
                logger.info(f"✅ Found shared folder: {SHARED_DRIVE_FOLDER}")
                return files[0]['id']
            
            # Create folder if it doesn't exist
            logger.info(f"📁 Creating folder: {SHARED_DRIVE_FOLDER}")
            file_metadata = {
                'name': SHARED_DRIVE_FOLDER,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            logger.info(f"✅ Created folder: {SHARED_DRIVE_FOLDER} (ID: {folder_id})")
            return folder_id
            
        except Exception as e:
            logger.error(f"Error finding/creating folder: {e}")
            return None
    
    async def load_shared_data(self) -> Optional[Dict]:
        """Load shared data from Google Drive"""
        if not self.service:
            logger.error("No Drive service available")
            return None
        
        if not self.folder_id:
            logger.error("No folder ID available")
            return None
        
        try:
            query = f"'{self.folder_id}' in parents and name='{SHARED_FILE_NAME}' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            
            if not files:
                logger.info(f"No shared file '{SHARED_FILE_NAME}' found")
                return None
            
            file_id = files[0]['id']
            logger.info(f"📥 Downloading: {SHARED_FILE_NAME}")
            
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"Download progress: {progress}%")
            
            fh.seek(0)
            data = json.loads(fh.read().decode('utf-8'))
            logger.info(f"✅ Loaded shared data: {data.get('total_urls', 0)} URLs from {data.get('date', 'unknown date')}")
            
            # Verify data structure
            if not data.get('topics'):
                logger.warning("Loaded data has no 'topics' field")
                return None
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in shared file: {e}")
            return None
        except Exception as e:
            logger.error(f"Load from Drive error: {e}")
            return None
    
    async def save_shared_data(self, data: Dict) -> bool:
        """Save shared data to Google Drive"""
        if not self.service or not self.folder_id:
            logger.error("No Drive service or folder available")
            return False
        
        try:
            # Check if file exists
            query = f"'{self.folder_id}' in parents and name='{SHARED_FILE_NAME}' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            
            # Prepare file content
            file_content = json.dumps(data, indent=2, ensure_ascii=False)
            media = MediaFileUpload(
                io.BytesIO(file_content.encode('utf-8')),
                mimetype='application/json',
                resumable=True
            )
            
            if files:
                # Update existing file
                file_id = files[0]['id']
                logger.info(f"📤 Updating: {SHARED_FILE_NAME}")
                self.service.files().update(
                    fileId=file_id,
                    media_body=media
                ).execute()
                logger.info(f"✅ Updated shared file: {SHARED_FILE_NAME}")
            else:
                # Create new file
                logger.info(f"📤 Creating: {SHARED_FILE_NAME}")
                file_metadata = {
                    'name': SHARED_FILE_NAME,
                    'parents': [self.folder_id]
                }
                self.service.files().create(
                    body=file_metadata,
                    media_body=media
                ).execute()
                logger.info(f"✅ Created shared file: {SHARED_FILE_NAME}")
            
            return True
            
        except Exception as e:
            logger.error(f"Save to Drive error: {e}")
            return False

















class FoundUrlsTracker:
    """Track found URLs from various sources"""
    
    def __init__(self):
        self.found_urls = self._load()
        self.data_source = "local"
    
    def _load(self) -> Dict:
        if os.path.exists(FOUND_URLS_FILE):
            try:
                with open(FOUND_URLS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save(self):
        with open(FOUND_URLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.found_urls, f, indent=2)
    
    def update(self, topics_data: Dict, source: str = "local"):
        """Update found URLs"""
        self.found_urls = {
            "last_updated": datetime.now().isoformat(),
            "topics": topics_data,
            "source": source
        }
        self.data_source = source
        self._save()
    
    def get_all_urls(self) -> List[str]:
        urls = []
        for topic, reels in self.found_urls.get("topics", {}).items():
            for reel in reels:
                if isinstance(reel, dict) and reel.get("url"):
                    urls.append(reel["url"])
        return urls
    
    def get_topic_urls(self, topic: str) -> List[str]:
        urls = []
        for reel in self.found_urls.get("topics", {}).get(topic, []):
            if isinstance(reel, dict) and reel.get("url"):
                urls.append(reel["url"])
        return urls
    
    def get_source(self) -> str:
        return self.data_source


class ReelTracker:
    """Track all reels - downloaded and pending"""
    
    def __init__(self):
        self.downloaded = self._load(DOWNLOADED_LOG_FILE)
        self.pending = self._load(PENDING_LOG_FILE)
    
    def _load(self, filename: str) -> Dict:
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        logger.info(f"Converting old list format to dict for {filename}")
                        converted = {}
                        for item in data:
                            if isinstance(item, dict):
                                shortcode = item.get('shortcode', '')
                                if shortcode:
                                    converted[shortcode] = item
                        self._save(filename, converted)
                        return converted
                    return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning(f"Failed to load {filename}: {e}")
                return {}
        return {}
    
    def _save(self, filename: str, data: Dict):
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    def is_downloaded(self, shortcode: str) -> bool:
        return shortcode in self.downloaded
    
    def mark_downloaded(self, shortcode: str, data: Dict):
        self.downloaded[shortcode] = {
            **data,
            "download_date": datetime.now().isoformat()
        }
        self._save(DOWNLOADED_LOG_FILE, self.downloaded)
        if shortcode in self.pending:
            del self.pending[shortcode]
            self._save(PENDING_LOG_FILE, self.pending)
    
    def save_pending(self, reels: List[Dict]):
        self.pending = {}
        for reel in reels:
            self.pending[reel['shortcode']] = {
                **reel,
                "discovered": datetime.now().isoformat()
            }
        self._save(PENDING_LOG_FILE, self.pending)
    
    def get_pending(self) -> List[Dict]:
        return list(self.pending.values())
    
    def clear_pending(self):
        self.pending = {}
        self._save(PENDING_LOG_FILE, self.pending)
    
    def get_stats(self) -> Dict:
        total = len(self.downloaded)
        today = 0
        for d in self.downloaded.values():
            if isinstance(d, dict):
                download_date = d.get("download_date", "")
                if download_date.startswith(datetime.now().strftime("%Y-%m-%d")):
                    today += 1
        pending = len(self.pending)
        return {
            "total_downloaded": total,
            "downloaded_today": today,
            "pending": pending
        }


class GoogleDriveUploader:
    """Upload to Google Drive"""
    
    def __init__(self):
        self.service = self._authenticate()
        self.folder_id = self._get_or_create_folder()
    
    def _authenticate(self):
        creds = None
        
        token_json = os.environ.get('GOOGLE_DRIVE_TOKEN')
        if token_json:
            try:
                token_data = json.loads(base64.b64decode(token_json).decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logger.info("Drive authenticated via env")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"Env token failed: {e}")
        
        if os.path.exists('drive_token.pickle'):
            try:
                with open('drive_token.pickle', 'rb') as f:
                    creds = pickle.load(f)
                logger.info("Drive authenticated via token")
                return build('drive', 'v3', credentials=creds)
            except:
                pass
        
        logger.error("No Drive credentials found")
        return None
    
    def _get_or_create_folder(self):
        if not self.service:
            return None
        
        try:
            query = f"name='{INSTAGRAM_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            
            if files:
                logger.info(f"Found folder: {INSTAGRAM_FOLDER_NAME}")
                return files[0]['id']
            
            file_metadata = {
                'name': INSTAGRAM_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            logger.info(f"Created folder: {INSTAGRAM_FOLDER_NAME}")
            return folder.get('id')
            
        except Exception as e:
            logger.error(f"Folder error: {e}")
            return None
    
    async def upload(self, file_path: str, metadata: Dict = None) -> Optional[Dict]:
        if not self.service or not self.folder_id:
            return None
        
        try:
            file_name = os.path.basename(file_path)
            logger.info(f"Uploading: {file_name}")
            
            file_metadata = {
                'name': file_name,
                'parents': [self.folder_id]
            }
            
            if metadata and metadata.get('full_description'):
                file_metadata['description'] = metadata['full_description'][:5000]
            
            media = MediaFileUpload(file_path, mimetype='video/mp4', resumable=True)
            
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink'
            ).execute()
            
            logger.info(f"Upload complete: {file.get('name')}")
            return file
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None


class IntelligentReelDownloader:
    """Intelligent downloader with preview before download"""
    
    def __init__(self):
        self.tracker = ReelTracker()
        self.drive = GoogleDriveUploader()
        self.drive_loader = SharedDriveLoader()
        self.found_urls = FoundUrlsTracker()
        self.current_pending = []
    
    def extract_shortcode(self, url: str) -> Optional[str]:
        match = re.search(r'instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)', url)
        return match.group(1) if match else None
    
    async def fetch_reels(self) -> Dict:
        """Fetch reels from Google Drive first, then fallback to Reel Finder"""
        
        results = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "topics": {},
            "total": 0,
            "new": 0,
            "already_downloaded": 0,
            "reel_finder_url": REEL_FINDER_URL,
            "data_source": "unknown"
        }
        
        all_reels = []
        found_topics = {}
        
        # FIRST: Try to load from Google Drive shared file
        shared_data = await self.drive_loader.load_shared_data()
        
        if shared_data and shared_data.get("topics"):
            logger.info(f"✅ Loaded {shared_data.get('total_urls', 0)} URLs from Google Drive")
            
            found_topics = shared_data.get("topics", {})
            results["date"] = shared_data.get("date", datetime.now().strftime("%Y-%m-%d"))
            results["data_source"] = "google_drive"
            
            # Process shared data
            for topic, reels_data in found_topics.items():
                topic_reels = []
                for reel_data in reels_data:
                    shortcode = reel_data.get("shortcode", "")
                    if not shortcode:
                        continue
                    
                    url = reel_data.get("url", "")
                    is_downloaded = self.tracker.is_downloaded(shortcode)
                    
                    reel_info = {
                        "shortcode": shortcode,
                        "url": url,
                        "topic": topic,
                        "is_downloaded": is_downloaded,
                        "status": "downloaded" if is_downloaded else "pending"
                    }
                    
                    topic_reels.append(reel_info)
                    all_reels.append(reel_info)
                    
                    if not is_downloaded:
                        results["new"] += 1
                    else:
                        results["already_downloaded"] += 1
                    
                    results["total"] += 1
                
                results["topics"][topic] = topic_reels
            
            # Save found URLs to local cache
            self.found_urls.update(found_topics, "google_drive")
            
            # Save pending reels
            pending = [r for r in all_reels if not r["is_downloaded"]]
            self.tracker.save_pending(pending)
            self.current_pending = pending
            
            return results
        
        # SECOND: Fallback to Reel Finder API
        logger.info("No shared data found, fetching from Reel Finder API")
        
        topics = ["mafia", "gangstars", "murphy", "war", "ninjas"]
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{REEL_FINDER_URL}/api/topics")
                if response.status_code == 200:
                    data = response.json()
                    if data.get("topics"):
                        topics = data["topics"]
                        logger.info(f"Using topics from Reel Finder: {topics}")
        except Exception as e:
            logger.warning(f"Could not fetch topics from Reel Finder: {e}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for topic in topics:
                try:
                    response = await client.get(f"{REEL_FINDER_URL}/urls?topic={topic}")
                    if response.status_code != 200:
                        logger.warning(f"Failed to fetch {topic}: {response.status_code}")
                        continue
                    
                    data = response.json()
                    urls = data.get("urls", [])
                    
                    topic_reels = []
                    for url in urls:
                        shortcode = self.extract_shortcode(url)
                        if not shortcode:
                            continue
                        
                        is_downloaded = self.tracker.is_downloaded(shortcode)
                        
                        reel_info = {
                            "shortcode": shortcode,
                            "url": url,
                            "topic": topic,
                            "is_downloaded": is_downloaded,
                            "status": "downloaded" if is_downloaded else "pending"
                        }
                        
                        topic_reels.append(reel_info)
                        all_reels.append(reel_info)
                        
                        if not is_downloaded:
                            results["new"] += 1
                        else:
                            results["already_downloaded"] += 1
                        
                        results["total"] += 1
                    
                    results["topics"][topic] = topic_reels
                    found_topics[topic] = topic_reels
                    
                except Exception as e:
                    logger.error(f"Error fetching {topic}: {e}")
        
        results["data_source"] = "reel_finder_api"
        
        # Save found URLs
        self.found_urls.update(found_topics, "reel_finder_api")
        
        # Save pending reels
        pending = [r for r in all_reels if not r["is_downloaded"]]
        self.tracker.save_pending(pending)
        self.current_pending = pending
        
        return results
    
    async def download_pending(self, shortcodes: List[str] = None) -> Dict:
        """Download pending reels (all or specific ones)"""
        pending = self.tracker.get_pending()
        
        if shortcodes:
            pending = [p for p in pending if p['shortcode'] in shortcodes]
        
        if not pending:
            return {
                "success": True,
                "message": "No pending reels to download",
                "downloaded": 0,
                "skipped": 0,
                "failed": 0
            }
        
        results = {
            "total": len(pending),
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "details": []
        }
        
        for reel in pending:
            shortcode = reel['shortcode']
            url = reel['url']
            
            if self.tracker.is_downloaded(shortcode):
                results["skipped"] += 1
                results["details"].append({
                    "shortcode": shortcode,
                    "status": "skipped",
                    "reason": "already downloaded"
                })
                continue
            
            try:
                logger.info(f"Downloading: {shortcode}")
                
                ydl_opts = {
                    "format": "best",
                    "quiet": True,
                    "no_warnings": True,
                    "cookiefile": COOKIE_FILE if os.path.exists(COOKIE_FILE) else None,
                    "outtmpl": f"{shortcode}.%(ext)s",
                }
                
                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        filename = ydl.prepare_filename(info)
                        return info, filename
                
                info, filename = await asyncio.to_thread(download)
                
                description = info.get('description', '')
                hashtags = re.findall(r'#(\w+)', description)
                
                metadata = {
                    "shortcode": shortcode,
                    "url": url,
                    "username": info.get('uploader', 'Unknown'),
                    "caption": description[:500],
                    "likes": info.get('like_count', 0),
                    "comments": info.get('comment_count', 0),
                    "hashtags": hashtags,
                    "full_description": f"📝 {description[:1000]}\n\n👤 @{info.get('uploader', 'Unknown')}\n❤️ {info.get('like_count', 0)} likes\n📥 Downloaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
                
                file = await self.drive.upload(filename, metadata)
                
                if os.path.exists(filename):
                    os.remove(filename)
                
                if file:
                    self.tracker.mark_downloaded(shortcode, {
                        "url": url,
                        "username": metadata['username'],
                        "drive_file_id": file.get('id'),
                        "drive_link": file.get('webViewLink')
                    })
                    
                    results["downloaded"] += 1
                    results["details"].append({
                        "shortcode": shortcode,
                        "status": "downloaded",
                        "drive_link": file.get('webViewLink')
                    })
                else:
                    results["failed"] += 1
                    results["details"].append({
                        "shortcode": shortcode,
                        "status": "failed",
                        "reason": "upload failed"
                    })
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error downloading {shortcode}: {e}")
                results["failed"] += 1
                results["details"].append({
                    "shortcode": shortcode,
                    "status": "failed",
                    "reason": str(e)
                })
        
        self.tracker.clear_pending()
        
        return results


downloader = IntelligentReelDownloader()


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
async def index():
    stats = downloader.tracker.get_stats()
    found_urls = downloader.found_urls.found_urls
    data_source = downloader.found_urls.get_source()
    return await render_template_string(HTML_TEMPLATE, 
                                       stats=stats, 
                                       folder=INSTAGRAM_FOLDER_NAME, 
                                       reel_finder_url=REEL_FINDER_URL,
                                       found_urls=found_urls,
                                       data_source=data_source)

@app.route("/api/fetch", methods=["POST"])
async def fetch_reels():
    try:
        results = await downloader.fetch_reels()
        results["reel_finder_url"] = REEL_FINDER_URL
        return jsonify({
            "success": True,
            **results
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/download", methods=["POST"])
async def download_reels():
    try:
        data = await request.get_json()
        shortcodes = data.get("shortcodes", [])
        results = await downloader.download_pending(shortcodes)
        return jsonify({
            "success": True,
            **results
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats")
async def get_stats():
    return jsonify(downloader.tracker.get_stats())

@app.route("/api/pending")
async def get_pending():
    return jsonify({
        "success": True,
        "pending": downloader.tracker.get_pending()
    })

@app.route("/api/found-urls")
async def get_found_urls():
    return jsonify({
        "source": downloader.found_urls.get_source(),
        "data": downloader.found_urls.found_urls
    })

@app.route("/api/found-urls/all")
async def get_all_found_urls():
    return jsonify({
        "urls": downloader.found_urls.get_all_urls(),
        "count": len(downloader.found_urls.get_all_urls()),
        "source": downloader.found_urls.get_source()
    })

@app.route("/api/reel-finder-url")
async def get_reel_finder_url():
    return jsonify({
        "url": REEL_FINDER_URL,
        "environment": "render" if os.environ.get("RENDER") else "local"
    })

@app.route("/health")
async def health():
    return {
        "status": "healthy",
        "service": "Intelligent Reel Downloader",
        "reel_finder_url": REEL_FINDER_URL,
        "environment": "render" if os.environ.get("RENDER") else "local",
        "data_source": downloader.found_urls.get_source()
    }


# -----------------------------
# HTML Template with Found URLs Display
# -----------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Intelligent Reel Downloader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0f; color: #fff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: flex-start; min-height: 100vh; padding: 20px; }
        .container { background: #1a1a1a; padding: 30px; border-radius: 16px; max-width: 1000px; width: 100%; }
        .reel-finder-info { 
            background: #2a2a2a; 
            padding: 10px 15px; 
            border-radius: 8px; 
            margin-bottom: 15px;
            font-size: 13px;
            color: #888;
            border-left: 3px solid #dc2743;
        }
        .reel-finder-info strong { color: #4ade80; }
        .reel-finder-info .source-badge {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            margin-left: 8px;
        }
        .source-badge.gdrive { background: #3b82f6; color: #fff; }
        .source-badge.api { background: #8b5cf6; color: #fff; }
        .source-badge.local { background: #6b7280; color: #fff; }
        h1 { color: #dc2743; font-size: 28px; display: flex; align-items: center; gap: 10px; }
        .subtitle { color: #888; margin: 5px 0 20px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }
        .stat-card { background: #2a2a2a; padding: 15px; border-radius: 10px; text-align: center; }
        .stat-number { font-size: 28px; font-weight: bold; color: #dc2743; }
        .stat-label { color: #888; font-size: 13px; }
        .btn-group { display: flex; gap: 10px; flex-wrap: wrap; margin: 20px 0; }
        .btn { padding: 12px 24px; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; color: #fff; }
        .btn-primary { background: linear-gradient(135deg, #dc2743, #bc1888); }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(220, 39, 67, 0.4); }
        .btn-success { background: linear-gradient(135deg, #10b981, #059669); }
        .btn-success:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(16, 185, 129, 0.4); }
        .btn-secondary { background: #2a2a2a; border: 1px solid #444; }
        .btn-secondary:hover { background: #333; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
        .reel-list { margin: 20px 0; }
        .reel-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: #2a2a2a; border-radius: 8px; margin: 5px 0; transition: all 0.2s; }
        .reel-item:hover { background: #333; }
        .reel-item .shortcode { font-family: monospace; color: #4ade80; }
        .reel-item .topic { color: #888; font-size: 12px; background: #1a1a1a; padding: 2px 10px; border-radius: 20px; }
        .badge { padding: 2px 12px; border-radius: 20px; font-size: 11px; }
        .badge-pending { background: #f59e0b; color: #000; }
        .badge-downloaded { background: #10b981; color: #000; }
        .badge-url { background: #3b82f6; color: #fff; }
        .checkbox { width: 20px; height: 20px; cursor: pointer; accent-color: #dc2743; }
        .status-box { padding: 15px; border-radius: 10px; margin: 15px 0; display: none; }
        .status-box.success { display: block; background: #10b98120; border: 1px solid #10b981; }
        .status-box.error { display: block; background: #ef444420; border: 1px solid #ef4444; }
        .status-box.info { display: block; background: #3b82f620; border: 1px solid #3b82f6; }
        .progress-bar { width: 100%; height: 4px; background: #2a2a2a; border-radius: 2px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #dc2743, #bc1888); width: 0%; transition: width 0.5s; }
        .footer { margin-top: 20px; color: #666; font-size: 12px; text-align: center; border-top: 1px solid #2a2a2a; padding-top: 20px; }
        .select-all { display: flex; align-items: center; gap: 10px; margin: 10px 0; color: #888; }
        .found-urls-section { margin-top: 20px; }
        .found-urls-section details { cursor: pointer; }
        .found-urls-section summary { color: #dc2743; font-weight: bold; padding: 10px; background: #2a2a2a; border-radius: 8px; }
        .url-item { 
            padding: 4px 10px; 
            border-bottom: 1px solid #333; 
            font-size: 12px; 
            display: flex; 
            justify-content: space-between;
            align-items: center;
        }
        .url-item .url { color: #4ade80; word-break: break-all; }
        @media (max-width: 600px) {
            .container { padding: 20px; }
            .stats-grid { grid-template-columns: 1fr 1fr; }
            .reel-item { flex-wrap: wrap; gap: 8px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 Intelligent Reel Downloader</h1>
        <p class="subtitle">Preview reels before downloading • Powered by Reel Finder & Google Drive</p>
        
        <div class="reel-finder-info">
            🔗 Connected to: <strong>{{ reel_finder_url }}</strong>
            <span style="margin-left: 10px; font-size: 11px; color: #666;">
                ({{ '✅ Render' if 'onrender.com' in reel_finder_url else '🖥️ Local' }})
            </span>
            <span class="source-badge {% if data_source == 'google_drive' %}gdrive{% elif data_source == 'reel_finder_api' %}api{% else %}local{% endif %}">
                {% if data_source == 'google_drive' %}📁 Google Drive{% elif data_source == 'reel_finder_api' %}🌐 API{% else %}💾 Local{% endif %}
            </span>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number" id="statTotal">{{ stats.total_downloaded }}</div>
                <div class="stat-label">Total Downloaded</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="statToday">{{ stats.downloaded_today }}</div>
                <div class="stat-label">Downloaded Today</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="statPending">{{ stats.pending }}</div>
                <div class="stat-label">Pending</div>
            </div>
        </div>
        
        <div class="btn-group">
            <button class="btn btn-primary" onclick="fetchReels()">🔍 Fetch & Preview</button>
            <button class="btn btn-success" onclick="downloadSelected()" id="downloadBtn">⬇️ Download Selected</button>
            <button class="btn btn-secondary" onclick="downloadAll()">📥 Download All</button>
            <button class="btn btn-secondary" onclick="refreshStats()">🔄 Refresh</button>
        </div>
        
        <div id="statusBox" class="status-box"></div>
        
        <div id="progressContainer" style="display:none;">
            <div class="progress-bar">
                <div id="progressFill" class="progress-fill"></div>
            </div>
            <div id="progressText" style="color:#888;font-size:13px;"></div>
        </div>
        
        <div id="reelList">
            <div style="text-align:center;color:#888;padding:40px 0;">
                Click "Fetch & Preview" to see available reels
            </div>
        </div>
        
        <!-- Found URLs Section -->
        <div class="found-urls-section">
            <details>
                <summary>📋 Found URLs <span style="font-weight:normal;color:#888;font-size:12px;">({{ found_urls.topics|length if found_urls and found_urls.topics else 0 }} topics)</span></summary>
                <div style="margin-top: 10px; max-height: 300px; overflow-y: auto;">
                    <div id="foundUrlsContent">
                        <div style="text-align:center;color:#888;padding:20px;">
                            Click "Fetch & Preview" to load found URLs
                        </div>
                    </div>
                </div>
            </details>
        </div>
        
        <div class="footer">
            Topics: mafia, gangstars, murphy, war, ninjas • 5 reels each<br>
            <span style="color:#444;">📁 {{ folder }}</span>
        </div>
    </div>
    
    <script>
        let selectedReels = new Set();
        
        function showStatus(message, type = 'info') {
            const box = document.getElementById('statusBox');
            box.className = `status-box ${type}`;
            box.textContent = message;
        }
        
        function hideStatus() {
            document.getElementById('statusBox').className = 'status-box';
        }
        
        function updateProgress(percent, text) {
            const container = document.getElementById('progressContainer');
            container.style.display = 'block';
            document.getElementById('progressFill').style.width = percent + '%';
            document.getElementById('progressText').textContent = text || `${percent}%`;
        }
        
        function hideProgress() {
            document.getElementById('progressContainer').style.display = 'none';
        }
        
        function toggleReel(shortcode, checked) {
            if (checked) {
                selectedReels.add(shortcode);
            } else {
                selectedReels.delete(shortcode);
            }
            document.getElementById('downloadBtn').textContent = 
                `⬇️ Download Selected (${selectedReels.size})`;
        }
        
        function toggleAll(checked) {
            document.querySelectorAll('.reel-checkbox').forEach(cb => {
                cb.checked = checked;
                toggleReel(cb.value, checked);
            });
        }
        
        function renderFoundUrls(foundUrls) {
            const container = document.getElementById('foundUrlsContent');
            
            if (!foundUrls || !foundUrls.topics || Object.keys(foundUrls.topics).length === 0) {
                container.innerHTML = '<div style="text-align:center;color:#888;padding:20px;">No URLs found yet. Click "Fetch & Preview".</div>';
                return;
            }
            
            let html = '';
            let total = 0;
            
            for (const [topic, reels] of Object.entries(foundUrls.topics)) {
                if (reels && reels.length > 0) {
                    total += reels.length;
                    html += `<div style="margin-top:10px;"><strong style="color:#dc2743;">#${topic}</strong> (${reels.length} URLs)`;
                    reels.forEach(reel => {
                        html += `
                            <div class="url-item">
                                <span class="url">${reel.url}</span>
                                <span class="badge badge-url">${reel.shortcode}</span>
                            </div>
                        `;
                    });
                    html += '</div>';
                }
            }
            
            if (total === 0) {
                container.innerHTML = '<div style="text-align:center;color:#888;padding:20px;">No URLs found</div>';
            } else {
                container.innerHTML = `
                    <div style="color:#888;margin-bottom:10px;">Total: <strong style="color:#fff;">${total}</strong> URLs found</div>
                    <div style="color:#888;font-size:11px;margin-bottom:5px;">Source: ${foundUrls.source || 'unknown'}</div>
                    ${html}
                `;
            }
        }
        
        async function fetchReels() {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = '⏳ Fetching...';
            hideStatus();
            showStatus('⏳ Fetching reels from Google Drive & Reel Finder...', 'info');
            
            try {
                const response = await fetch('/api/fetch', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`✅ Found ${data.new} new reels (${data.already_downloaded} already downloaded) from ${data.data_source}`, 'success');
                    renderReels(data);
                    updateStats();
                    
                    // Also update found URLs
                    const foundResponse = await fetch('/api/found-urls');
                    const foundData = await foundResponse.json();
                    renderFoundUrls(foundData.data);
                    
                    // Update source badge
                    updateSourceBadge(foundData.source);
                } else {
                    showStatus(`❌ Error: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`❌ Error: ${error.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = '🔍 Fetch & Preview';
            }
        }
        
        function updateSourceBadge(source) {
            const badge = document.querySelector('.source-badge');
            if (!badge) return;
            
            badge.className = 'source-badge';
            if (source === 'google_drive') {
                badge.classList.add('gdrive');
                badge.textContent = '📁 Google Drive';
            } else if (source === 'reel_finder_api') {
                badge.classList.add('api');
                badge.textContent = '🌐 API';
            } else {
                badge.classList.add('local');
                badge.textContent = '💾 Local';
            }
        }
        
        function renderReels(data) {
            const container = document.getElementById('reelList');
            selectedReels.clear();
            
            if (!data.topics || Object.keys(data.topics).length === 0) {
                container.innerHTML = '<div style="text-align:center;color:#888;padding:40px 0;">No reels found</div>';
                return;
            }
            
            let html = '';
            let totalNew = 0;
            
            html += `
                <div class="select-all">
                    <input type="checkbox" class="checkbox" onchange="toggleAll(this.checked)">
                    <span>Select All</span>
                </div>
            `;
            
            for (const [topic, reels] of Object.entries(data.topics)) {
                html += `<div style="margin-top:15px;"><strong style="color:#dc2743;">#${topic}</strong>`;
                
                reels.forEach(reel => {
                    const isNew = !reel.is_downloaded;
                    if (isNew) totalNew++;
                    
                    html += `
                        <div class="reel-item">
                            <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
                                <input type="checkbox" class="checkbox reel-checkbox" 
                                    value="${reel.shortcode}" 
                                    ${isNew ? '' : 'disabled'}
                                    onchange="toggleReel('${reel.shortcode}', this.checked)">
                                <span class="shortcode">${reel.shortcode}</span>
                                <span class="topic">${topic}</span>
                            </div>
                            <div>
                                <span class="badge ${isNew ? 'badge-pending' : 'badge-downloaded'}">
                                    ${isNew ? '⏳ Pending' : '✅ Downloaded'}
                                </span>
                            </div>
                        </div>
                    `;
                });
                
                html += `</div>`;
            }
            
            container.innerHTML = html;
            document.getElementById('downloadBtn').textContent = `⬇️ Download Selected (0)`;
            
            if (totalNew === 0) {
                showStatus('🎉 All reels are already downloaded!', 'success');
            }
        }
        
        async function downloadSelected() {
            if (selectedReels.size === 0) {
                showStatus('⚠️ Please select at least one reel to download', 'error');
                return;
            }
            
            const shortcodes = Array.from(selectedReels);
            await performDownload(shortcodes);
        }
        
        async function downloadAll() {
            const pending = document.querySelectorAll('.reel-checkbox:not(:disabled)');
            if (pending.length === 0) {
                showStatus('⚠️ No pending reels to download', 'error');
                return;
            }
            
            const shortcodes = Array.from(pending).map(cb => cb.value);
            await performDownload(shortcodes);
        }
        
        async function performDownload(shortcodes) {
            const btn = event ? event.target : document.getElementById('downloadBtn');
            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '⏳ Downloading...';
            hideStatus();
            showStatus(`⏳ Downloading ${shortcodes.length} reels...`, 'info');
            updateProgress(0, 'Starting download...');
            
            try {
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ shortcodes })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`✅ Downloaded: ${data.downloaded}, Skipped: ${data.skipped}, Failed: ${data.failed}`, 'success');
                    updateProgress(100, 'Complete!');
                    
                    setTimeout(() => {
                        fetchReels();
                    }, 1000);
                } else {
                    showStatus(`❌ Error: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`❌ Error: ${error.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
                setTimeout(hideProgress, 3000);
                updateStats();
            }
        }
        
        async function updateStats() {
            try {
                const response = await fetch('/api/stats');
                const stats = await response.json();
                document.getElementById('statTotal').textContent = stats.total_downloaded;
                document.getElementById('statToday').textContent = stats.downloaded_today;
                document.getElementById('statPending').textContent = stats.pending;
            } catch (error) {
                console.error('Stats error:', error);
            }
        }
        
        async function refreshStats() {
            await updateStats();
            showStatus('🔄 Stats refreshed', 'info');
            setTimeout(hideStatus, 2000);
        }
        
        // Load found URLs on page load
        async function loadFoundUrls() {
            try {
                const response = await fetch('/api/found-urls');
                const data = await response.json();
                renderFoundUrls(data.data);
                updateSourceBadge(data.source);
            } catch (error) {
                console.error('Error loading found URLs:', error);
            }
        }
        
        // Initial load
        setTimeout(fetchReels, 1000);
        loadFoundUrls();
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    print(f"🚀 Starting server on http://localhost:{PORT}")
    print(f"📌 Connected to Reel Finder: {REEL_FINDER_URL}")
    print("📌 Will load URLs from Google Drive first, then fallback to API")
    print("📌 Use the UI to fetch and download reels")
    app.run(host="0.0.0.0", port=PORT, debug=False)