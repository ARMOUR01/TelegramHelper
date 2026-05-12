#!/usr/bin/env bash
# Первичный setup на fly.io. Запускать ОДИН раз после `fly auth login`.
#
# Что делает:
# 1. Создаёт приложение (если ещё нет).
# 2. Создаёт persistent volume `telegramhelper_data` в primary_region.
# 3. Загружает все переменные из локального .env как fly secrets.
# 4. Делает первый деплой.
#
# Дальнейшие обновления — `fly deploy` (без аргументов).

set -euo pipefail

APP_NAME="${FLY_APP_NAME:-telegramhelper}"
REGION="${FLY_REGION:-ams}"
ENV_FILE="${ENV_FILE:-.env}"

if ! command -v fly >/dev/null 2>&1; then
  echo "❌ flyctl не найден. Установи: brew install flyctl (mac) или curl -L https://fly.io/install.sh | sh"
  exit 1
fi

if ! fly auth whoami >/dev/null 2>&1; then
  echo "❌ Не залогинен в fly.io. Выполни: fly auth login"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ Не нашёл $ENV_FILE — скопируй .env.example в .env и заполни"
  exit 1
fi

echo "▶ App: $APP_NAME · Region: $REGION · Env: $ENV_FILE"

# 1) приложение
if ! fly status -a "$APP_NAME" >/dev/null 2>&1; then
  echo "▶ Создаю app $APP_NAME…"
  fly apps create "$APP_NAME" --org personal
else
  echo "✔ App $APP_NAME уже существует"
fi

# 2) volume
if ! fly volumes list -a "$APP_NAME" 2>/dev/null | grep -q "telegramhelper_data"; then
  echo "▶ Создаю volume telegramhelper_data в $REGION (3GB)…"
  fly volumes create telegramhelper_data --region "$REGION" --size 3 -a "$APP_NAME" --yes
else
  echo "✔ Volume telegramhelper_data уже есть"
fi

# 3) secrets из .env
echo "▶ Заливаю secrets из $ENV_FILE…"
SECRETS=()
while IFS= read -r line; do
  # пропускаем пустые и комментарии
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  # вырезаем потенциальные кавычки вокруг значения
  key="${line%%=*}"
  val="${line#*=}"
  val="${val%\"}"
  val="${val#\"}"
  val="${val%\'}"
  val="${val#\'}"
  # DATABASE_URL не нужен — он прибит в fly.toml [env]
  [[ "$key" == "DATABASE_URL" ]] && continue
  [[ -z "$key" || -z "$val" ]] && continue
  SECRETS+=("$key=$val")
done < "$ENV_FILE"

if [[ ${#SECRETS[@]} -gt 0 ]]; then
  fly secrets set --stage -a "$APP_NAME" "${SECRETS[@]}"
fi

# 4) деплой
echo "▶ Деплою…"
fly deploy -a "$APP_NAME" --remote-only

echo "✅ Готово. Логи: fly logs -a $APP_NAME · Статус: fly status -a $APP_NAME"
