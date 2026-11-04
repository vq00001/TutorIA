# TutorIA

The frontend lives in `Webapp/`. It is a React + TypeScript + Vite SPA; its current data adapter simulates JSON responses in memory. The parent directory is the Docker Compose project root so a backend can be added as a sibling service later.

## Run the frontend with Docker

From this directory:

```bash
docker compose up --build
```

Open <http://localhost:8080>. Set `WEBAPP_PORT` in the shell or a root `.env` file to change the host port, for example `WEBAPP_PORT=8090`.

The `webapp` image uses a multi-stage build: Node compiles the Vite app, then Nginx serves the static output. Nginx falls back to `index.html` for React Router URLs and reserves `/api/` as the backend proxy path.

## Add the backend later

Add a `backend` service to the root `compose.yaml`, built from a future sibling directory such as `Backend/`. It should listen on container port `8000`; the frontend proxy resolves `backend:8000` through the Compose network. Browser code should call relative URLs such as `/api/v1/courses`, so the browser only needs to reach the frontend origin. The Compose service name is for container-to-container communication and should not be used as a hostname in browser code.

The frontend's API boundary and DTOs are documented in [Webapp/docs/data-contract.md](Webapp/docs/data-contract.md). Its mock adapter must later be replaced with HTTP requests to the `/api` proxy.

## Run without Docker

```bash
cd Webapp
npm ci
npm run dev
```
