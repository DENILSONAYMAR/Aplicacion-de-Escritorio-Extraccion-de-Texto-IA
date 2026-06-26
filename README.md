# Document AI Desktop

Aplicacion de escritorio para cargar PDFs o imagenes, extraer datos con IA, revisar resultados y exportarlos a Excel.

## Estructura

```text
backend/      FastAPI, PostgreSQL, OCR, OpenAI y exportacion a Excel
desktop-app/  React + Vite + Tauri
infra/        Docker Compose para backend y base de datos
```

Los archivos de uso local se generan en `storage/` y no se suben al repositorio.

## Requisitos

- Git
- Docker Desktop
- Node.js 20 o superior
- Rust estable, necesario para compilar Tauri
- Una API key de OpenAI

## Instalacion local

1. Clona el repositorio:

```bash
git clone https://github.com/TU_USUARIO/document-ai-desktop.git
cd document-ai-desktop
```

2. Crea el archivo de entorno del backend:

```bash
cp backend/.env.example backend/.env
```

En Windows PowerShell:

```powershell
Copy-Item backend/.env.example backend/.env
```

Edita `backend/.env` y reemplaza `OPENAI_API_KEY` por tu clave real.

3. Levanta PostgreSQL y el backend:

```bash
docker compose -f infra/docker-compose.yml up --build
```

El backend quedara disponible en `http://127.0.0.1:8000`.

4. En otra terminal, instala y ejecuta la app de escritorio:

```bash
cd desktop-app
npm install
npm run tauri dev
```

Para ejecutar solo el frontend web:

```bash
npm run dev
```

## Configuracion opcional

Si el backend corre en otra URL, crea `desktop-app/.env` desde el ejemplo:

```bash
cp desktop-app/.env.example desktop-app/.env
```

Y ajusta:

```env
VITE_API_URL=http://127.0.0.1:8000
```

## Subir a GitHub

1. Crea un repositorio vacio en GitHub.
2. Desde esta carpeta ejecuta:

```bash
git init
git add .
git commit -m "Initial project cleanup"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/document-ai-desktop.git
git push -u origin main
```

Si este proyecto ya tiene Git iniciado, usa desde `git add .`.

## Archivos que no deben subirse

El `.gitignore` excluye:

- `.env` y secretos
- `storage/`, `uploads/`, `exports/`, `page_images/`
- `node_modules/`
- `dist/`
- `target/`
- `__pycache__/`
- logs y archivos temporales del sistema
