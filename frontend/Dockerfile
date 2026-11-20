# syntax=docker/dockerfile:1
FROM node:24.20.0-alpine AS dependencies
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 npm ci

FROM dependencies AS dev
COPY --chown=node:node . .
EXPOSE 8080
CMD ["npm", "run", "dev"]

FROM dev AS build
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31.7-alpine AS prod
ENV BACKEND_UPSTREAM=http://backend:8000 AGENT_UPSTREAM=http://agent:8001
COPY nginx.conf /etc/nginx/neti.conf.template
COPY --from=build /app/dist/ /usr/share/nginx/html/
EXPOSE 8080
CMD ["sh", "-ec", "export UPSTREAM_DNS=${UPSTREAM_DNS:-$(awk '$1 == \"nameserver\" { print $2; exit }' /etc/resolv.conf)}; envsubst '$UPSTREAM_DNS $BACKEND_UPSTREAM $AGENT_UPSTREAM' </etc/nginx/neti.conf.template >/tmp/neti.conf; exec nginx -c /tmp/neti.conf -g 'daemon off;'"]
