# フロントエンド（React）のビルドと、Nginxによる配信
# ビルド段階：静的ファイルを生成する
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# 配信段階：Nginxで静的ファイル配信・/apiの中継・HTTPS終端を行う
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 443
