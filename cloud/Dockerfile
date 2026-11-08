# Image for AWS (ECS Express Mode). Kept simple on purpose: the whole app with
# its node_modules, so Prisma's migration tool is there at start-up.
FROM node:22-slim AS build
WORKDIR /app
# Prisma's migration engine needs OpenSSL.
RUN apt-get update -y && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
COPY prisma ./prisma
COPY prisma.config.ts ./
# prisma.config.ts reads DATABASE_URL; nothing connects during the build.
ENV DATABASE_URL=postgresql://build:build@localhost:5432/build
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev --ignore-scripts

FROM node:22-slim
WORKDIR /app
RUN apt-get update -y && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production PORT=3000
COPY --from=build /app ./
# The prisma CLI is a dev dependency, so bring it back for migrations.
COPY --from=build /app/package.json ./package.json
RUN npm install --no-save --ignore-scripts prisma@7.10.1 && npm cache clean --force
EXPOSE 3000
# Apply any new migrations, then start. Migrations are idempotent.
CMD ["sh", "-c", "npx prisma migrate deploy && npx next start -p ${PORT}"]
