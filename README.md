# 🦂 SCORPIO | High-Performance IG Extraction Engine
![System Version](https://img.shields.io/badge/System-v2.5_Online-indigo?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-emerald?style=for-the-badge)
![Engine](https://img.shields.io/badge/Engine-CORE--V2-blue?style=for-the-badge)

**Scorpio** is a professional-grade, asynchronous Instagram downloader. Built with a cyber-ops aesthetic, it utilizes a **Quart** backend and **yt-dlp** to deliver original-source media streams directly to the user with zero server-side storage overhead.



---

## ⚡ Core Specifications

| Feature | Specification |
| :--- | :--- |
| **Backend Architecture** | Asynchronous Python (Quart) |
| **Stream Logic** | 1MB Fragmented Chunking (`httpx`) |
| **UI Framework** | Tailwind CSS + Glassmorphism v3 |
| **Extraction Engine** | `yt-dlp` Core-V2 Optimized |
| **Stability** | Integrated Anti-Sleep Heartbeat Engine |

---

## 💎 Exclusive Features

### 🛠️ Anti-Sleep Heartbeat
Optimized for **Render** and **Heroku** deployments. Scorpio includes an automated background task that pings the system `/health` endpoint every 10 minutes, effectively bypassing the 15-minute inactivity "idle sleep" on free-tier hosting.

### 🧬 System Recalibration
Upon every initialization or manual refresh, the engine executes a protocol to clear the data gateway and reset the extraction pipeline. This ensures a 100% optimized state for every new request.

### ⏱️ 6-Second Auto-Reset
To maintain maximum system reliability, Scorpio features an automated session reset. After a fetch execution, the system performs a hard reset of the interface exactly **6 seconds** later, preventing memory leaks and ensuring the extraction engine remains primed for high-speed use.

---

## 🚀 Rapid Deployment

### 1. Configure Environment
Set these variables in your deployment dashboard (Render/Fly.io/Heroku):

* `COOKIES_CONTENT`: Raw text content from your exported Netscape cookies file.
* `PUBLIC_URL`: Your live application URL (e.g., `https://scorpio.onrender.com/health`).
* `PORT`: `5000`

### 2. Local Installation
Copy and execute the following commands in your terminal to deploy the engine:

```bash
# Clone the repository
git clone [https://github.com/Motari2004/igdown](https://github.com/Motari2004/igdown)

# Enter the system directory
cd igdown

# Install the extraction stack
pip install quart yt-dlp httpx

# Boot the engine
python app.py"# instagramyoutube" 
"# igyoutube" 
"# ignewyoutube" 
"# ignewyoutube" 
