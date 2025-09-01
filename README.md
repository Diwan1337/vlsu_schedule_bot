# 🎓 VLSU Schedule Bot

Telegram-бот для просмотра расписания ВлГУ. Парсит официальное API и предоставляет удобный интерфейс для студентов.

## ✨ Возможности

- 📅 Просмотр расписания на неделю
- 🔍 Поиск группы по названию  
- 📱 Удобный интерактивный интерфейс
- 🔄 Автоматическое обновление данных
- 🏫 Поддержка всех институтов и форм обучения

## 🚀 Быстрый запуск

### 1. Клонирование репозитория

```
git clone https://github.com/yourusername/vlsu_schedule_bot.git
cd vlsu_schedule_bot
```
2. Настройка окружения
# Скопируйте шаблон настроек
```
cp .env.example .env
```

# Отредактируйте .env файл (замените your_telegram_bot_token_here на реальный токен)

3. Запуск с Docker (рекомендуется)
# Сборка и запуск
```
docker-compose up -d
```

# Просмотр логов
```
docker-compose logs -f vlsu-bot
```
4. Запуск без Docker
# Создание виртуального окружения
```
python -m venv .venv
```
```
source .venv/bin/activate  # Linux/Mac
```
# или
```
.\.venv\Scripts\activate   # Windows
```
# Установка зависимостей
```
pip install -r requirements.txt
```

# Запуск бота
```
python -m app.bot
```
⚙️ Конфигурация
Переменные окружения (.env)
Переменная|По умолчанию|Описание
BOT_TOKEN|Токен Telegram бота|(обязательно)
DB_PATH|data/vlsu_schedule.db|Путь к базе данных
TZ|Europe/Moscow|Часовой пояс

Настройка парсера
# Просмотр всех доступных команд парсера
```
python -m app.bulk_parse --help
```

# Запуск парсера с определенными параметрами
```
python -m app.bulk_parse \
  --db data/vlsu_schedule.db \
  --forms 0 1 2 \
  --pause 0.3 \
  --debug
```
🐳 Docker команды
Основные команды
# Запуск бота
```
docker-compose up -d vlsu-bot

# Остановка бота  
docker-compose down

# Просмотр логов
docker-compose logs -f vlsu-bot

# Обновление данных расписания
docker-compose run --rm updater --forms 0

# Вход в контейнер
docker-compose exec vlsu-bot sh
```

Обновление данных
```
# Обновление всех форм обучения
docker-compose run --rm updater --forms 0 1 2

# Обновление только очной формы
docker-compose run --rm updater --forms 0

# Обновление с отладкой
docker-compose run --rm updater --forms 0 --debug

# Обновление конкретного института
docker-compose run --rm updater --forms 0 --only-institute "институт_id"
```
📊 Команды парсера
```
# Полное обновление базы
python -m app.bulk_parse --db data/vlsu_schedule.db --forms 0 1 2

# Только очная форма
python -m app.bulk_parse --db data/vlsu_schedule.db --forms 0

# С отладочным выводом
python -m app.bulk_parse --db data/vlsu_schedule.db --forms 0 --debug

# С увеличенной паузой между запросами
python -m app.bulk_parse --db data/vlsu_schedule.db --forms 0 --pause 0.5
```
```
# Обновление конкретного института
python -m app.bulk_parse --db data/vlsu_schedule.db \
  --forms 0 \
  --only-institute "институт_id"

# Обновление с фильтром по названию института  
python -m app.cli dump-universe \
  --db data/vlsu_schedule.db \
  --name-like "Институт"
```

🗄️ Управление базой данных
```
# Просмотр статистики базы
docker-compose exec vlsu-bot sqlite3 /app/data/vlsu_schedule.db "
SELECT 
  (SELECT COUNT(*) FROM institutes) as institutes,
  (SELECT COUNT(*) FROM groups) as groups, 
  (SELECT COUNT(*) FROM lessons) as lessons;
"

# Резервное копирование базы
docker-compose exec vlsu-bot cp /app/data/vlsu_schedule.db /app/backup.db

# Восстановление из резервной копии
docker-compose exec vlsu-bot cp /app/backup.db /app/data/vlsu_schedule.db
```

🔧 Разработка
Структура проекта
```
vlsu_schedule_bot/
├── app/
│   ├── bot.py              # Основной код бота
│   ├── bulk_parse.py       # Парсер расписания
│   ├── cli.py             # CLI утилиты
│   ├── vlsu_api.py        # API ВлГУ
│   └── storage/
│       ├── db.py          # Функции работы с БД
│       └── models.py      # Модели SQLAlchemy
├── data/                   # Базы данных (не в git)
├── .env.example           # Шаблон настроек
├── requirements.txt       # Зависимости Python
├── docker-compose.yml     # Docker конфигурация
└── Dockerfile            # Образ Docker
```

Примечание: Не забывайте обновлять расписание перед началом семестра! 🎓
