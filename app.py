"""
Intelligent Daily Reel Downloader - Preview URLs before downloading
"""

import os
import asyncio
import logging
import sys
import json
import re
import base64
import pickle
from datetime import datetime
from typing import List, Dict, Optional

from quart import Quart, request, jsonify, render_template_string
import yt_dlp
import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# -----------------------------
# Configuration
# -----------------------------
PORT = int(os.environ.get("PORT", 5000))
REEL_FINDER_URL = os.environ.get("REEL_FINDER_URL", "https://reelfinder.onrender.com")
COOKIE_FILE = "instagram_cookies.txt"

# Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive']
INSTAGRAM_FOLDER_NAME = "Instagram_Daily_Reels"
DOWNLOADED_LOG_FILE = "downloaded_reels.json"
PENDING_LOG_FILE = "pending_reels.json"

# Handle Cookies
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








class ReelTracker:
    """Track all reels - downloaded and pending"""
    
    def __init__(self):
        self.downloaded = self._load(DOWNLOADED_LOG_FILE)
        self.pending = self._load(PENDING_LOG_FILE)
    
    def _load(self, filename: str) -> Dict:
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    data = json.load(f)
                    # Handle old list format
                    if isinstance(data, list):
                        logger.info(f"📋 Converting old list format to dict for {filename}")
                        converted = {}
                        for item in data:
                            if isinstance(item, dict):
                                shortcode = item.get('shortcode', '')
                                if shortcode:
                                    converted[shortcode] = item
                        # Save converted format
                        self._save(filename, converted)
                        return converted
                    return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning(f"Failed to load {filename}: {e}")
                return {}
        return {}
    
    def _save(self, filename: str, data: Dict):
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
    
    def is_downloaded(self, shortcode: str) -> bool:
        return shortcode in self.downloaded
    
    def mark_downloaded(self, shortcode: str, data: Dict):
        self.downloaded[shortcode] = {
            **data,
            "download_date": datetime.now().isoformat()
        }
        self._save(DOWNLOADED_LOG_FILE, self.downloaded)
        # Remove from pending
        if shortcode in self.pending:
            del self.pending[shortcode]
            self._save(PENDING_LOG_FILE, self.pending)
    
    def save_pending(self, reels: List[Dict]):
        """Save pending reels for preview"""
        self.pending = {}
        for reel in reels:
            self.pending[reel['shortcode']] = {
                **reel,
                "discovered": datetime.now().isoformat()
            }
        self._save(PENDING_LOG_FILE, self.pending)
    
    def get_pending(self) -> List[Dict]:
        """Get all pending reels"""
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
                logger.info("✅ Drive authenticated via env")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"Env token failed: {e}")
        
        if os.path.exists('drive_token.pickle'):
            try:
                with open('drive_token.pickle', 'rb') as f:
                    creds = pickle.load(f)
                logger.info("✅ Drive authenticated via token")
                return build('drive', 'v3', credentials=creds)
            except:
                pass
        
        logger.error("❌ No Drive credentials found")
        return None
    
    def _get_or_create_folder(self):
        if not self.service:
            return None
        
        try:
            query = f"name='{INSTAGRAM_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])
            
            if files:
                logger.info(f"📁 Found folder: {INSTAGRAM_FOLDER_NAME}")
                return files[0]['id']
            
            file_metadata = {
                'name': INSTAGRAM_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            logger.info(f"📁 Created folder: {INSTAGRAM_FOLDER_NAME}")
            return folder.get('id')
            
        except Exception as e:
            logger.error(f"Folder error: {e}")
            return None
    
    async def upload(self, file_path: str, metadata: Dict = None) -> Optional[Dict]:
        if not self.service or not self.folder_id:
            return None
        
        try:
            file_name = os.path.basename(file_path)
            logger.info(f"📤 Uploading: {file_name}")
            
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
            
            logger.info(f"✅ Upload complete: {file.get('name')}")
            return file
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None


class IntelligentReelDownloader:
    """Intelligent downloader with preview before download"""
    
    def __init__(self):
        self.tracker = ReelTracker()
        self.drive = GoogleDriveUploader()
        self.current_pending = []
    
    def extract_shortcode(self, url: str) -> Optional[str]:
        match = re.search(r'instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)', url)
        return match.group(1) if match else None
    
    async def fetch_reels(self) -> Dict:
        """Fetch reels from Reel Finder without downloading"""
        topics = ["mafia", "gangstars", "murphy", "war", "ninjas"]
        
        results = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "topics": {},
            "total": 0,
            "new": 0,
            "already_downloaded": 0
        }
        
        all_reels = []
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for topic in topics:
                try:
                    response = await client.get(f"{REEL_FINDER_URL}/urls?topic={topic}")
                    if response.status_code != 200:
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
                    
                except Exception as e:
                    logger.error(f"Error fetching {topic}: {e}")
        
        # Save pending reels (only new ones)
        pending = [r for r in all_reels if not r["is_downloaded"]]
        self.tracker.save_pending(pending)
        self.current_pending = pending
        
        return results
    
    async def download_pending(self, shortcodes: List[str] = None) -> Dict:
        """Download pending reels (all or specific ones)"""
        pending = self.tracker.get_pending()
        
        if shortcodes:
            # Filter to specific shortcodes
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
            
            # Double-check not already downloaded
            if self.tracker.is_downloaded(shortcode):
                results["skipped"] += 1
                results["details"].append({
                    "shortcode": shortcode,
                    "status": "skipped",
                    "reason": "already downloaded"
                })
                continue
            
            try:
                logger.info(f"📥 Downloading: {shortcode}")
                
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
                
                # Extract metadata
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
                
                # Upload to Drive
                file = await self.drive.upload(filename, metadata)
                
                # Cleanup
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
                
                await asyncio.sleep(2)  # Rate limit
                
            except Exception as e:
                logger.error(f"Error downloading {shortcode}: {e}")
                results["failed"] += 1
                results["details"].append({
                    "shortcode": shortcode,
                    "status": "failed",
                    "reason": str(e)
                })
        
        # Clear pending after download
        self.tracker.clear_pending()
        
        return results


downloader = IntelligentReelDownloader()


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
async def index():
    stats = downloader.tracker.get_stats()
    return await render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Intelligent Reel Downloader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0f; color: #fff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: flex-start; min-height: 100vh; padding: 20px; }
        .container { background: #1a1a1a; padding: 30px; border-radius: 16px; max-width: 900px; width: 100%; }
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
        .badge-failed { background: #ef4444; color: #fff; }
        .badge-skipped { background: #6b7280; color: #fff; }
        .checkbox { width: 20px; height: 20px; cursor: pointer; accent-color: #dc2743; }
        .status-box { padding: 15px; border-radius: 10px; margin: 15px 0; display: none; }
        .status-box.success { display: block; background: #10b98120; border: 1px solid #10b981; }
        .status-box.error { display: block; background: #ef444420; border: 1px solid #ef4444; }
        .status-box.info { display: block; background: #3b82f620; border: 1px solid #3b82f6; }
        .progress-bar { width: 100%; height: 4px; background: #2a2a2a; border-radius: 2px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #dc2743, #bc1888); width: 0%; transition: width 0.5s; }
        .footer { margin-top: 20px; color: #666; font-size: 12px; text-align: center; border-top: 1px solid #2a2a2a; padding-top: 20px; }
        .select-all { display: flex; align-items: center; gap: 10px; margin: 10px 0; color: #888; }
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
        <p class="subtitle">Preview reels before downloading • Powered by Reel Finder</p>
        
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
        
        async function fetchReels() {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = '⏳ Fetching...';
            hideStatus();
            showStatus('⏳ Fetching reels from Reel Finder...', 'info');
            
            try {
                const response = await fetch('/api/fetch', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`✅ Found ${data.new} new reels (${data.already_downloaded} already downloaded)`, 'success');
                    renderReels(data);
                    updateStats();
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
        
        function renderReels(data) {
            const container = document.getElementById('reelList');
            selectedReels.clear();
            
            if (!data.topics || Object.keys(data.topics).length === 0) {
                container.innerHTML = '<div style="text-align:center;color:#888;padding:40px 0;">No reels found</div>';
                return;
            }
            
            let html = '';
            let totalNew = 0;
            
            // Select All checkbox
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
                    
                    // Refresh the view
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
        
        // Auto-fetch on load
        setTimeout(fetchReels, 1000);
    </script>
</body>
</html>
    """, stats=downloader.tracker.get_stats(), folder=INSTAGRAM_FOLDER_NAME)


@app.route("/api/fetch", methods=["POST"])
async def fetch_reels():
    """Fetch reels from Reel Finder for preview"""
    try:
        results = await downloader.fetch_reels()
        return jsonify({
            "success": True,
            **results
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/download", methods=["POST"])
async def download_reels():
    """Download selected pending reels"""
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
    """Get all pending reels"""
    return jsonify({
        "success": True,
        "pending": downloader.tracker.get_pending()
    })


@app.route("/health")
async def health():
    return {"status": "healthy", "service": "Intelligent Reel Downloader"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)