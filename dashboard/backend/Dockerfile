# Backend image. Build context is the repo root (see docker-compose.yml).
FROM node:22-slim AS build
RUN corepack enable
WORKDIR /app
COPY . .
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @l2/backend build

FROM node:22-slim
RUN corepack enable
WORKDIR /app
COPY --from=build /app /app
ENV NODE_ENV=production
ENV PORT=8787
EXPOSE 8787
CMD ["node", "backend/dist/index.js"]
