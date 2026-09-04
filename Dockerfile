FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/index.html ./index.html
COPY frontend/public ./public
COPY frontend/src ./src
RUN node -e "const p=require('./package.json'); if (p.devDependencies.vite !== '5.4.14') { throw new Error('AssignmentTracker requires Vite 5.4.14 for Raspberry Pi ARM builds; received ' + p.devDependencies.vite) }"
RUN npm install --no-audit --no-fund --include=optional \
    && npm ls vite @vitejs/plugin-react \
    && npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
RUN mkdir -p /app/data
ENV ASSIGNMENTTRACKER_DB=/app/data/assignmenttracker.db
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
