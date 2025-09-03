# 🎓 VLSU Schedule Bot 🚧🛠️🔧

**Telegram-бот для просмотра расписания ВлГУ.**  
Парсит официальное API университета и предоставляет удобный интерфейс для студентов.

---

## ✨ Возможности

- 📅 Просмотр расписания на неделю  
- 🔍 Поиск группы по названию  
- 📱 Интерактивный интерфейс с кнопками  
- 🔄 Автоматическое обновление данных  
- 🏫 Поддержка всех институтов и форм обучения  

---

## 🚀 Быстрый запуск

### 1. Клонирование репозитория
```bash
git clone https://github.com/yourusername/vlsu_schedule_bot.git
cd vlsu_schedule_bot
```

### 2. Настройка окружения
🚧🛠️🔧
Впишите **BOT_TOKEN** от BotFather.

### 3. Запуск с Docker (рекомендуется)
```bash
docker-compose up -d
```

Просмотр логов:
```bash
docker-compose logs -f vlsu-bot
```

### 4. Запуск без Docker
```bash
python -m venv .venv
source .venv/bin/activate     # Linux/Mac
.\.venv\Scriptsctivate      # Windows

pip install -r requirements.txt
python -m app.bot
```

