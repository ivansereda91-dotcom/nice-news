#!/bin/bash
# Установка бота новостей Ниццы: bash /opt/nice-news/setup.sh
set -e
APP=/opt/nice-news
DATA=/var/lib/nice-news

echo "== Ставлю зависимости =="
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q python3-venv >/dev/null
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"
mkdir -p "$DATA"

if [ ! -f "$APP/.env" ]; then
  echo
  read -rsp "Вставь токен нового бота от BotFather (символы не видны) и нажми Enter: " TOKEN; echo
  [ -n "$TOKEN" ] || { echo "Токен пустой, запусти ещё раз"; exit 1; }
  # ключ Claude и список пользователей берём у французского бота
  AKEY=$(grep ^ANTHROPIC_API_KEY= /opt/french-bot/.env | cut -d= -f2-)
  IDS=$(grep ^ALLOWED_IDS= /opt/french-bot/.env | cut -d= -f2-)
  cat > "$APP/.env" <<EOF
BOT_TOKEN=$TOKEN
ANTHROPIC_API_KEY=$AKEY
ALLOWED_IDS=$IDS
DB_PATH=$DATA/news.db
NEWS_TIME=08:00
AFISHA_DAY=3
AFISHA_TIME=18:00
CLAUDE_MODEL=claude-haiku-5-5
EOF
  chmod 600 "$APP/.env"
fi

cat > /etc/systemd/system/nice-news.service <<EOF
[Unit]
Description=Telegram bot: Nice news and events
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nice-news >/dev/null 2>&1
systemctl restart nice-news
sleep 3
systemctl is-active nice-news
echo
echo "Готово. Напиши новому боту /start, потом /news."
