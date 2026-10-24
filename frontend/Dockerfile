FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Les réécritures /api de Next.js sont figées à la compilation : l'adresse interne de l'API est fournie ici.
ARG HC_API_URL=http://api:8000
ENV HC_API_URL=$HC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app ./
USER node
CMD ["npm", "start"]
