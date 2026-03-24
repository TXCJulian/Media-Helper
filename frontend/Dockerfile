# 1. Build Stage
FROM node:20-alpine AS builder

WORKDIR /app

# Kopiere Package-Manifeste und installiere Dependencies
COPY package*.json ./
RUN npm ci

# Quellcode kopieren und Production-Build erzeugen
COPY . .
RUN npm run build

# 2. Runtime Stage
FROM nginx:1.29-alpine-slim

# Statische Dateien aus dem Builder-Image übernehmen
COPY --from=builder /app/dist /usr/share/nginx/html

# Eigene Nginx-Konfiguration, die auf Port 3000 lauscht
COPY nginx-app.conf /etc/nginx/conf.d/default.conf

# Container-intern benutzt Nginx Port 3000
EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]