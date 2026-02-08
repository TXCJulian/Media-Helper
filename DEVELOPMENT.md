# Lokale Entwicklung

## Voraussetzungen
- **Python 3.11+**
- **Node.js 20.19+ oder 22.12+**
- **TMDB API Key** (für Episode Renaming)

## Setup

### 1. Backend Setup

```powershell
# Wechsel ins Backend-Verzeichnis
cd backend

# Python Virtual Environment erstellen
python -m venv venv

# Virtual Environment aktivieren
.\venv\Scripts\Activate.ps1

# Dependencies installieren
pip install -r requirements.txt

# Environment-Datei erstellen
copy .env.development dependencies\.env

# dependencies/.env bearbeiten und ausfüllen:
# - BASE_PATH: Pfad zu deinem Test-Medien-Ordner (z.B. E:/TestMedia)
# - TMDB_API_KEY: Dein TMDB API Key

# Backend starten (läuft auf http://localhost:8000)
cd app
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup

```powershell
# Neues Terminal öffnen
cd frontend

# Dependencies installieren
npm install

# Frontend starten (läuft auf http://localhost:5173)
npm run dev
```

### 3. Öffnen

Frontend öffnet sich automatisch auf: **http://localhost:5173**

Das Frontend nutzt den Vite-Proxy und leitet alle API-Requests automatisch an `http://localhost:8000` weiter.

## Entwicklungs-Workflow

### Hot Reload
- **Backend**: Änderungen werden durch `--reload` automatisch neu geladen
- **Frontend**: Vite reloaded automatisch bei Dateiänderungen

### API-Verbindung testen
Öffne http://localhost:5173 und überprüfe in den Browser DevTools (Network-Tab), ob die API-Requests erfolgreich sind.

### Debugging
- **Backend-Logs**: Im Terminal wo `uvicorn` läuft
- **Frontend-Logs**: Browser DevTools Console (F12)

## Ordnerstruktur für Tests

Erstelle einen Test-Ordner mit folgender Struktur:

```
E:/TestMedia/
├── TV Shows/
│   └── Breaking Bad/
│       └── Season 01/
│           ├── episode1.mkv
│           └── episode2.mkv
└── Music/
    └── Artist Name/
        └── Album Name/
            ├── 01-track.flac
            └── 02-track.flac
```

## Troubleshooting

### Backend startet nicht
- Prüfe ob Port 8000 frei ist: `netstat -an | findstr :8000`
- Prüfe ob `.env` korrekt in `backend/dependencies/.env` liegt
- Prüfe Python-Version: `python --version` (min. 3.11)

### Frontend kann Backend nicht erreichen
- Prüfe ob Backend auf http://localhost:8000 läuft
- Öffne http://localhost:8000/docs für FastAPI Swagger UI
- Prüfe Browser Console auf CORS-Fehler

### CORS-Fehler
Das Backend erlaubt bereits alle Origins (`allow_origins=["*"]`). Wenn trotzdem CORS-Fehler auftreten, stelle sicher dass beide Server laufen.

## Production Build testen

```powershell
# Frontend bauen
cd frontend
npm run build

# Production-Preview starten
npm run preview
```

## Docker Testing

```powershell
# Beide Container lokal bauen und starten
docker-compose up --build

# Nur Backend testen
docker build -t media-renamer:backend ./backend
docker run -p 8000:3332 -v E:/TestMedia:/media media-renamer:backend
```
