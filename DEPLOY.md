# 🌐 Как открыть сайт для всех

## ⚡ Способ 1: Ngrok (САМЫЙ ПРОСТОЙ, 2 минуты)

Ngrok создаёт временный публичный URL для вашего локального сервера.

### Шаг 1: Установка ngrok

**Windows (через Chocolatey):**
```bash
choco install ngrok
```

**Mac (через Homebrew):**
```bash
brew install ngrok
```

**Linux:**
```bash
sudo snap install ngrok
```

**Или скачайте вручную:**
1. Перейдите на https://ngrok.com/download
2. Скачайте и распакуйте
3. Добавьте в PATH

### Шаг 2: Запуск

```bash
# 1. Запустите Flask сервер (в одном терминале)
python main.py

# 2. В другом терминале запустите ngrok
ngrok http 5000
```

### Шаг 3: Получите URL

Ngrok покажет что-то вроде:
```
Forwarding  https://abc123-def456.ngrok.io -> http://localhost:5000
```

Отправьте ссылку `https://abc123-def456.ngrok.io` друзьям! 🎉

**Важно:** URL временный (работает пока запущен ngrok). Для постоянного URL нужен аккаунт ngrok (бесплатный).

---

## 🆓 Способ 2: PythonAnywhere (БЕСПЛАТНЫЙ ХОСТИНГ)

### Шаг 1: Регистрация
1. Перейдите на https://www.pythonanywhere.com
2. Зарегистрируйтесь (бесплатно)

### Шаг 2: Создание веб-приложения
1. Войдите в аккаунт
2. Перейдите во вкладку "Web"
3. Нажмите "Add a new web app"
4. Выберите "Flask" и Python 3.9
5. Укажите путь к файлу: `/home/ВАШ_ЛОГИН/olymp_platform/main.py`

### Шаг 3: Загрузка файлов
1. Перейдите во вкладку "Files"
2. Создайте папку `olymp_platform`
3. Загрузите все файлы проекта

### Шаг 4: Установка зависимостей
1. Откройте "Consoles" → "Bash"
2. Выполните:
```bash
pip3 install --user flask flask-socketio flask-login werkzeug eventlet
```

### Шаг 5: Перезагрузка
Вернитесь в "Web" и нажмите "Reload". Готово!

---

## 🚀 Способ 3: Heroku (БЕСПЛАТНЫЙ ХОСТИНГ)

### Шаг 1: Подготовка

Создайте файл `Procfile` (без расширения):
```
web: gunicorn -k eventlet -w 1 main:app
```

Обновите `requirements.txt`:
```
flask==3.0.0
flask-socketio==5.3.6
flask-login==0.6.3
werkzeug==3.0.1
eventlet==0.35.2
gunicorn==21.2.0
```

Создайте `runtime.txt`:
```
python-3.9.18
```

### Шаг 2: Регистрация и деплой

1. Зарегистрируйтесь на https://heroku.com
2. Установите Heroku CLI: https://devcenter.heroku.com/articles/heroku-cli
3. В терминале проекта:

```bash
# Логин
heroku login

# Создание приложения
heroku create ваше-название

# Деплой
git init
git add .
git commit -m "Initial commit"
git push heroku main
```

---

## 🖥️ Способ 4: Свой сервер (VPS)

Если у вас есть VPS (DigitalOcean, AWS, etc):

### Установка
```bash
# Подключитесь к серверу по SSH
ssh user@your-server-ip

# Установите Python и git
sudo apt update
sudo apt install python3-pip git

# Клонируйте проект
git clone https://github.com/ваш-репозиторий/olymp_platform.git
cd olymp_platform

# Установите зависимости
pip3 install -r requirements.txt

# Запустите
python3 main.py
```

### Для постоянной работы (через systemd)

Создайте файл `/etc/systemd/system/olymp.service`:
```ini
[Unit]
Description=Olympiad Platform
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/olymp_platform
ExecStart=/usr/bin/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Запустите:
```bash
sudo systemctl enable olymp
sudo systemctl start olymp
```

---

## 📋 Быстрая шпаргалка

| Способ | Сложность | Цена | Время работы |
|--------|-----------|------|--------------|
| Ngrok | ⭐ Легко | Бесплатно | Пока запущен |
| PythonAnywhere | ⭐⭐ Средне | Бесплатно | 24/7 |
| Heroku | ⭐⭐ Средне | Бесплатно | 24/7 (спит) |
| VPS | ⭐⭐⭐ Сложно | Платно | 24/7 |

---

## 💡 Рекомендации

- **Для демо/теста:** Используйте **ngrok** (быстрее всего)
- **Для учебного проекта:** Используйте **PythonAnywhere** (бесплатно и просто)
- **Для серьезного проекта:** Используйте **VPS** или **Heroku**

---

## ❓ Проблемы и решения

### Ngrok: "Session Status: online" но сайт не открывается
- Проверьте, что Flask сервер запущен на порту 5000
- Попробуйте: `ngrok http 5000 --host-header="localhost:5000"`

### PythonAnywhere: WebSocket не работает
- На бесплатном тарифе WebSocket ограничен
- PvP режим может работать некорректно

### Heroku: Приложение "спит"
- Бесплатный тариф: приложение засыпает после 30 мин без активности
- Первый запрос может быть медленным (пробуждение)
