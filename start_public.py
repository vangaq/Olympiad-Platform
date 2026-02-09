"""
Запуск сервера с публичным доступом через ngrok
"""

import subprocess
import sys
import time

# =========================================================
# 🔧 НАСТРОЙКИ (МЕНЯТЬ ЗДЕСЬ)
# =========================================================

PROJECT_NAME = "Olympiad Platform"   # Название проекта (для вывода)
SERVER_FILE = "main.py"              # Файл с Flask/FastAPI
SERVER_PORT = "5000"                 # Порт сервера
NGROK_COMMAND = "ngrok"              # Команда ngrok (если в PATH)

SERVER_START_DELAY = 3               # Секунды ожидания запуска сервера

# =========================================================
# 🚀 ОСНОВНОЙ КОД
# =========================================================

def main():
    print("=" * 60)
    print(f"🚀 Запуск {PROJECT_NAME} с публичным доступом")
    print("=" * 60)

    # Проверка ngrok
    try:
        result = subprocess.run(
            [NGROK_COMMAND, "version"],
            capture_output=True,
            text=True
        )
        print(f"✅ Ngrok найден: {result.stdout.strip()}")
    except FileNotFoundError:
        print("❌ Ngrok не найден!")
        print("📥 Установи ngrok и добавь его в PATH")
        print("👉 https://ngrok.com/download")
        return

    # Запуск сервера
    print("\n🔄 Запуск сервера...")
    server_process = subprocess.Popen(
        [sys.executable, SERVER_FILE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    print(f"⏳ Ожидание запуска сервера ({SERVER_START_DELAY} сек)...")
    time.sleep(SERVER_START_DELAY)

    # Запуск ngrok
    print("\n🌐 Запуск ngrok туннеля...")
    print("=" * 60)

    try:
        subprocess.run([
            NGROK_COMMAND,
            "http",
            SERVER_PORT
        ])
    except KeyboardInterrupt:
        print("\n\n⛔ Остановка серверов...")
        server_process.terminate()
        print("✅ Серверы остановлены")

# =========================================================

if __name__ == "__main__":
    main()
