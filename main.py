"""
Olympiad Training Platform
Запуск: python main.py
Открыть в браузере: http://localhost:5000
"""

import sqlite3
import subprocess
import tempfile
import os
import json
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Конфигурация
app = Flask(__name__)
app.config['SECRET_KEY'] = 'olymp-platform-secret-key-2024'
app.config['DATABASE'] = 'database.db'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Инициализация расширений
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Глобальные переменные для PvP
pvp_rooms = {}  # {room_id: {player1_id, player2_id, task_id, status, results}}


# ==================== МОДЕЛИ И БАЗА ДАННЫХ ====================

class User(UserMixin):
    def __init__(self, id, username, email, password_hash, is_admin=False, rating=1000):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.rating = rating


def get_db():
    """Получение соединения с БД"""
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    """Закрытие соединения с БД"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Инициализация базы данных"""
    db = get_db()
    
    # Таблица пользователей
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT 0,
            rating INTEGER DEFAULT 1000,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица задач
    db.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            difficulty INTEGER DEFAULT 1,
            input_format TEXT,
            output_format TEXT,
            sample_input TEXT,
            sample_output TEXT,
            test_cases TEXT,  -- JSON с тестами
            time_limit INTEGER DEFAULT 1000,  -- в миллисекундах
            memory_limit INTEGER DEFAULT 256,  -- в МБ
            category TEXT DEFAULT 'general',
            author_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (author_id) REFERENCES users (id)
        )
    ''')
    
    # Таблица решений
    db.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            language TEXT DEFAULT 'python',
            status TEXT DEFAULT 'pending',  -- pending, accepted, wrong_answer, timeout, error
            execution_time INTEGER,
            memory_used INTEGER,
            error_message TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
    ''')
    
    # Таблица соревнований PvP
    db.execute('''
        CREATE TABLE IF NOT EXISTS pvp_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            winner_id INTEGER,
            player1_time INTEGER,
            player2_time INTEGER,
            status TEXT DEFAULT 'active',  -- active, finished, cancelled
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (player1_id) REFERENCES users (id),
            FOREIGN KEY (player2_id) REFERENCES users (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
    ''')
    
    # Таблица прогресса пользователей
    db.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            solved BOOLEAN DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            first_solved_at TIMESTAMP,
            last_attempt_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id),
            UNIQUE(user_id, task_id)
        )
    ''')
    
    db.commit()
    
    # Создание администратора по умолчанию
    admin = db.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        db.execute(
            'INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)',
            ('admin', 'admin@platform.com', generate_password_hash('admin123'), True)
        )
        db.commit()
        print("Создан администратор: admin / admin123")
    
    # Добавление тестовых задач если их нет
    tasks_count = db.execute('SELECT COUNT(*) as count FROM tasks').fetchone()['count']
    if tasks_count == 0:
        sample_tasks = [
            {
                'title': 'Сумма двух чисел',
                'description': 'Напишите программу, которая считывает два целых числа и выводит их сумму.',
                'difficulty': 1,
                'input_format': 'Два целых числа a и b (-1000 ≤ a, b ≤ 1000)',
                'output_format': 'Одно число — сумма a и b',
                'sample_input': '3 5',
                'sample_output': '8',
                'test_cases': json.dumps([
                    {'input': '3 5', 'output': '8'},
                    {'input': '10 20', 'output': '30'},
                    {'input': '-5 5', 'output': '0'},
                    {'input': '0 0', 'output': '0'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Четное или нечетное',
                'description': 'Определите, является ли число четным или нечетным.',
                'difficulty': 1,
                'input_format': 'Одно целое число n',
                'output_format': 'Выведите "even" если число четное, "odd" если нечетное',
                'sample_input': '4',
                'sample_output': 'even',
                'test_cases': json.dumps([
                    {'input': '4', 'output': 'even'},
                    {'input': '7', 'output': 'odd'},
                    {'input': '0', 'output': 'even'},
                    {'input': '-3', 'output': 'odd'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Факториал',
                'description': 'Вычислите факториал числа n (n!).',
                'difficulty': 2,
                'input_format': 'Одно целое число n (0 ≤ n ≤ 10)',
                'output_format': 'Факториал числа n',
                'sample_input': '5',
                'sample_output': '120',
                'test_cases': json.dumps([
                    {'input': '0', 'output': '1'},
                    {'input': '1', 'output': '1'},
                    {'input': '5', 'output': '120'},
                    {'input': '10', 'output': '3628800'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Простое число',
                'description': 'Проверьте, является ли число простым.',
                'difficulty': 3,
                'input_format': 'Одно целое число n (2 ≤ n ≤ 1000)',
                'output_format': 'Выведите "yes" если число простое, "no" если составное',
                'sample_input': '7',
                'sample_output': 'yes',
                'test_cases': json.dumps([
                    {'input': '2', 'output': 'yes'},
                    {'input': '4', 'output': 'no'},
                    {'input': '17', 'output': 'yes'},
                    {'input': '100', 'output': 'no'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Максимум из трех',
                'description': 'Найдите максимальное из трех целых чисел.',
                'difficulty': 1,
                'input_format': 'Три целых числа a, b, c',
                'output_format': 'Максимальное число',
                'sample_input': '5 3 7',
                'sample_output': '7',
                'test_cases': json.dumps([
                    {'input': '5 3 7', 'output': '7'},
                    {'input': '10 10 5', 'output': '10'},
                    {'input': '-1 -5 -3', 'output': '-1'},
                    {'input': '0 0 0', 'output': '0'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Сумма цифр',
                'description': 'Найдите сумму цифр заданного числа.',
                'difficulty': 2,
                'input_format': 'Одно целое число n (0 ≤ n ≤ 1000000)',
                'output_format': 'Сумма цифр числа',
                'sample_input': '123',
                'sample_output': '6',
                'test_cases': json.dumps([
                    {'input': '123', 'output': '6'},
                    {'input': '5', 'output': '5'},
                    {'input': '999', 'output': '27'},
                    {'input': '0', 'output': '0'},
                    {'input': '1000', 'output': '1'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Перевернуть строку',
                'description': 'Выведите строку в обратном порядке.',
                'difficulty': 1,
                'input_format': 'Одна строка без пробелов',
                'output_format': 'Строка в обратном порядке',
                'sample_input': 'hello',
                'sample_output': 'olleh',
                'test_cases': json.dumps([
                    {'input': 'hello', 'output': 'olleh'},
                    {'input': 'abc', 'output': 'cba'},
                    {'input': 'a', 'output': 'a'},
                    {'input': '12345', 'output': '54321'}
                ]),
                'category': 'strings'
            },
            {
                'title': 'Палиндром',
                'description': 'Проверьте, является ли строка палиндромом (читается одинаково слева направо и справа налево).',
                'difficulty': 2,
                'input_format': 'Одна строка без пробелов',
                'output_format': 'Выведите "yes" если палиндром, "no" если нет',
                'sample_input': 'radar',
                'sample_output': 'yes',
                'test_cases': json.dumps([
                    {'input': 'radar', 'output': 'yes'},
                    {'input': 'hello', 'output': 'no'},
                    {'input': 'level', 'output': 'yes'},
                    {'input': 'a', 'output': 'yes'},
                    {'input': 'ab', 'output': 'no'}
                ]),
                'category': 'strings'
            },
            {
                'title': 'Количество слов',
                'description': 'Подсчитайте количество слов в строке. Слова разделены пробелами.',
                'difficulty': 1,
                'input_format': 'Строка со словами, разделенными пробелами',
                'output_format': 'Количество слов',
                'sample_input': 'hello world',
                'sample_output': '2',
                'test_cases': json.dumps([
                    {'input': 'hello world', 'output': '2'},
                    {'input': 'one', 'output': '1'},
                    {'input': 'a b c d e', 'output': '5'},
                    {'input': '', 'output': '0'}
                ]),
                'category': 'strings'
            },
            {
                'title': 'Минимальный элемент',
                'description': 'Найдите минимальный элемент в списке чисел.',
                'difficulty': 2,
                'input_format': 'На первой строке n - количество чисел. На второй строке n чисел.',
                'output_format': 'Минимальное число',
                'sample_input': '5\n3 1 4 1 5',
                'sample_output': '1',
                'test_cases': json.dumps([
                    {'input': '5\n3 1 4 1 5', 'output': '1'},
                    {'input': '1\n42', 'output': '42'},
                    {'input': '3\n-5 -10 -3', 'output': '-10'},
                    {'input': '4\n0 0 0 0', 'output': '0'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Сумма массива',
                'description': 'Вычислите сумму всех элементов массива.',
                'difficulty': 1,
                'input_format': 'На первой строке n - количество чисел. На второй строке n чисел.',
                'output_format': 'Сумма всех чисел',
                'sample_input': '3\n1 2 3',
                'sample_output': '6',
                'test_cases': json.dumps([
                    {'input': '3\n1 2 3', 'output': '6'},
                    {'input': '1\n5', 'output': '5'},
                    {'input': '4\n10 20 30 40', 'output': '100'},
                    {'input': '3\n-1 -2 -3', 'output': '-6'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Поиск элемента',
                'description': 'Найдите индекс элемента x в массиве (индексация с 0). Если элемента нет, выведите -1.',
                'difficulty': 2,
                'input_format': 'На первой строке n и x. На второй строке n чисел.',
                'output_format': 'Индекс элемента x или -1',
                'sample_input': '5 3\n1 2 3 4 5',
                'sample_output': '2',
                'test_cases': json.dumps([
                    {'input': '5 3\n1 2 3 4 5', 'output': '2'},
                    {'input': '5 10\n1 2 3 4 5', 'output': '-1'},
                    {'input': '3 1\n1 1 1', 'output': '0'},
                    {'input': '1 42\n42', 'output': '0'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Числа Фибоначчи',
                'description': 'Выведите n-ое число Фибоначчи. F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2)',
                'difficulty': 2,
                'input_format': 'Одно число n (0 ≤ n ≤ 20)',
                'output_format': 'n-ое число Фибоначчи',
                'sample_input': '6',
                'sample_output': '8',
                'test_cases': json.dumps([
                    {'input': '0', 'output': '0'},
                    {'input': '1', 'output': '1'},
                    {'input': '6', 'output': '8'},
                    {'input': '10', 'output': '55'},
                    {'input': '20', 'output': '6765'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Степень двойки',
                'description': 'Проверьте, является ли число степенью двойки.',
                'difficulty': 2,
                'input_format': 'Одно целое число n (1 ≤ n ≤ 1000000)',
                'output_format': 'Выведите "yes" если степень двойки, "no" если нет',
                'sample_input': '8',
                'sample_output': 'yes',
                'test_cases': json.dumps([
                    {'input': '1', 'output': 'yes'},
                    {'input': '2', 'output': 'yes'},
                    {'input': '8', 'output': 'yes'},
                    {'input': '3', 'output': 'no'},
                    {'input': '100', 'output': 'no'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Количество делителей',
                'description': 'Найдите количество натуральных делителей числа n.',
                'difficulty': 3,
                'input_format': 'Одно целое число n (1 ≤ n ≤ 1000)',
                'output_format': 'Количество делителей',
                'sample_input': '12',
                'sample_output': '6',
                'test_cases': json.dumps([
                    {'input': '1', 'output': '1'},
                    {'input': '12', 'output': '6'},
                    {'input': '7', 'output': '2'},
                    {'input': '100', 'output': '9'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Удалить пробелы',
                'description': 'Удалите все пробелы из строки.',
                'difficulty': 1,
                'input_format': 'Строка с пробелами',
                'output_format': 'Строка без пробелов',
                'sample_input': 'hello world',
                'sample_output': 'helloworld',
                'test_cases': json.dumps([
                    {'input': 'hello world', 'output': 'helloworld'},
                    {'input': 'a b c', 'output': 'abc'},
                    {'input': 'no_spaces', 'output': 'no_spaces'},
                    {'input': '  spaces  ', 'output': 'spaces'}
                ]),
                'category': 'strings'
            },
            {
                'title': 'Сортировка по возрастанию',
                'description': 'Отсортируйте массив чисел по возрастанию.',
                'difficulty': 2,
                'input_format': 'На первой строке n - количество чисел. На второй строке n чисел.',
                'output_format': 'Отсортированные числа через пробел',
                'sample_input': '5\n5 2 8 1 9',
                'sample_output': '1 2 5 8 9',
                'test_cases': json.dumps([
                    {'input': '5\n5 2 8 1 9', 'output': '1 2 5 8 9'},
                    {'input': '3\n3 2 1', 'output': '1 2 3'},
                    {'input': '1\n42', 'output': '42'},
                    {'input': '4\n-1 -5 0 3', 'output': '-5 -1 0 3'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Уникальные элементы',
                'description': 'Выведите только уникальные элементы массива (без повторений).',
                'difficulty': 3,
                'input_format': 'На первой строке n - количество чисел. На второй строке n чисел.',
                'output_format': 'Уникальные числа через пробел (в порядке первого появления)',
                'sample_input': '6\n1 2 1 3 2 4',
                'sample_output': '1 2 3 4',
                'test_cases': json.dumps([
                    {'input': '6\n1 2 1 3 2 4', 'output': '1 2 3 4'},
                    {'input': '5\n1 1 1 1 1', 'output': '1'},
                    {'input': '3\n5 3 7', 'output': '5 3 7'},
                    {'input': '1\n42', 'output': '42'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Прямоугольник',
                'description': 'По ширине и высоте прямоугольника выведите его площадь и периметр.',
                'difficulty': 1,
                'input_format': 'Два целых числа w и h - ширина и высота',
                'output_format': 'Площадь и периметр через пробел',
                'sample_input': '5 3',
                'sample_output': '15 16',
                'test_cases': json.dumps([
                    {'input': '5 3', 'output': '15 16'},
                    {'input': '4 4', 'output': '16 16'},
                    {'input': '1 1', 'output': '1 4'},
                    {'input': '10 5', 'output': '50 30'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Треугольник',
                'description': 'По трем сторонам проверьте, может ли существовать треугольник.',
                'difficulty': 2,
                'input_format': 'Три целых числа a, b, c - стороны треугольника',
                'output_format': 'Выведите "yes" если треугольник существует, "no" если нет',
                'sample_input': '3 4 5',
                'sample_output': 'yes',
                'test_cases': json.dumps([
                    {'input': '3 4 5', 'output': 'yes'},
                    {'input': '1 1 3', 'output': 'no'},
                    {'input': '5 5 5', 'output': 'yes'},
                    {'input': '1 2 3', 'output': 'no'}
                ]),
                'category': 'math'
            },
            {
                'title': 'Среднее арифметическое',
                'description': 'Найдите среднее арифметическое массива чисел.',
                'difficulty': 1,
                'input_format': 'На первой строке n - количество чисел. На второй строке n чисел.',
                'output_format': 'Среднее арифметическое (целое число)',
                'sample_input': '4\n1 2 3 4',
                'sample_output': '2',
                'test_cases': json.dumps([
                    {'input': '4\n1 2 3 4', 'output': '2'},
                    {'input': '2\n5 5', 'output': '5'},
                    {'input': '3\n10 20 30', 'output': '20'},
                    {'input': '1\n42', 'output': '42'}
                ]),
                'category': 'arrays'
            },
            {
                'title': 'Перевод в верхний регистр',
                'description': 'Переведите строку в верхний регистр.',
                'difficulty': 1,
                'input_format': 'Строка из строчных букв',
                'output_format': 'Строка в верхнем регистре',
                'sample_input': 'hello',
                'sample_output': 'HELLO',
                'test_cases': json.dumps([
                    {'input': 'hello', 'output': 'HELLO'},
                    {'input': 'abc', 'output': 'ABC'},
                    {'input': 'xyz', 'output': 'XYZ'},
                    {'input': 'test', 'output': 'TEST'}
                ]),
                'category': 'strings'
            },
            {
                'title': 'Длина строки',
                'description': 'Найдите длину строки.',
                'difficulty': 1,
                'input_format': 'Одна строка',
                'output_format': 'Длина строки',
                'sample_input': 'hello',
                'sample_output': '5',
                'test_cases': json.dumps([
                    {'input': 'hello', 'output': '5'},
                    {'input': '', 'output': '0'},
                    {'input': 'a', 'output': '1'},
                    {'input': 'abcdef', 'output': '6'}
                ]),
                'category': 'strings'
            }
        ]
        
        for task in sample_tasks:
            db.execute('''
                INSERT INTO tasks (title, description, difficulty, input_format, output_format, 
                                 sample_input, sample_output, test_cases, category, author_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (task['title'], task['description'], task['difficulty'], task['input_format'],
                  task['output_format'], task['sample_input'], task['sample_output'],
                  task['test_cases'], task['category'], 1))
        
        db.commit()
        print(f"Добавлено {len(sample_tasks)} тестовых задач")


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        return User(user['id'], user['username'], user['email'], 
                   user['password_hash'], user['is_admin'], user['rating'])
    return None


def admin_required(f):
    """Декоратор для проверки прав администратора"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash('Доступ запрещен. Требуются права администратора.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== ПРОВЕРКА РЕШЕНИЙ ====================

def run_python_code(code, input_data, timeout=5):
    """
    Безопасное выполнение Python кода
    Возвращает: (success, output, error_message)
    """
    # Создаем временный файл с кодом
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        # Добавляем перенаправление ввода (input() работает!)
        safe_code = f"""import sys
from io import StringIO
sys.stdin = StringIO({repr(input_data)})

{code}
"""
        f.write(safe_code)
        temp_file = f.name
    
    try:
        # Запускаем код с ограничениями безопасности
        result = subprocess.run(
            ['python', temp_file],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip()
            # Обрезаем длинные сообщения об ошибках
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            return False, "", error_msg
        
        return True, result.stdout.strip(), ""
        
    except subprocess.TimeoutExpired:
        return False, "", "Превышено время выполнения (timeout)"
    except Exception as e:
        return False, "", str(e)
    finally:
        # Удаляем временный файл
        try:
            os.unlink(temp_file)
        except:
            pass


def check_solution(task_id, code):
    """
    Проверка решения задачи
    Возвращает: (status, details)
    """
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    
    if not task:
        return 'error', 'Задача не найдена'
    
    test_cases = json.loads(task['test_cases'])
    passed = 0
    failed_tests = []
    total_time = 0
    
    for i, test in enumerate(test_cases):
        start_time = datetime.now()
        success, output, error = run_python_code(code, test['input'])
        execution_time = (datetime.now() - start_time).total_seconds() * 1000
        total_time += execution_time
        
        if not success:
            return 'error', f'Ошибка выполнения на тесте {i+1}: {error}'
        
        # Нормализуем вывод (убираем лишние пробелы и переносы строк)
        normalized_output = ' '.join(output.split())
        normalized_expected = ' '.join(test['output'].split())
        
        if normalized_output == normalized_expected:
            passed += 1
        else:
            failed_tests.append({
                'test': i + 1,
                'input': test['input'],
                'expected': test['output'],
                'got': output
            })
    
    if passed == len(test_cases):
        return 'accepted', f'Все {len(test_cases)} тестов пройдены! Среднее время: {total_time/len(test_cases):.0f}мс'
    else:
        return 'wrong_answer', f'Пройдено {passed}/{len(test_cases)} тестов'


# ==================== МАРШРУТЫ ====================

@app.route('/')
def index():
    """Главная страница"""
    db = get_db()
    
    # Статистика
    stats = {
        'users': db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count'],
        'tasks': db.execute('SELECT COUNT(*) as count FROM tasks').fetchone()['count'],
        'submissions': db.execute('SELECT COUNT(*) as count FROM submissions').fetchone()['count'],
        'solved': db.execute('SELECT COUNT(*) as count FROM user_progress WHERE solved = 1').fetchone()['count']
    }
    
    # Топ пользователей
    top_users = db.execute('''
        SELECT username, rating FROM users 
        WHERE is_admin = 0 
        ORDER BY rating DESC LIMIT 10
    ''').fetchall()
    
    return render_template('index.html', stats=stats, top_users=top_users)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Регистрация"""
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        
        if not username or not email or not password:
            flash('Все поля обязательны для заполнения', 'error')
            return redirect(url_for('register'))
        
        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов', 'error')
            return redirect(url_for('register'))
        
        db = get_db()
        
        # Проверка уникальности
        if db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
            flash('Пользователь с таким именем уже существует', 'error')
            return redirect(url_for('register'))
        
        if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Пользователь с таким email уже существует', 'error')
            return redirect(url_for('register'))
        
        # Создание пользователя
        password_hash = generate_password_hash(password)
        db.execute(
            'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
            (username, email, password_hash)
        )
        db.commit()
        
        flash('Регистрация успешна! Теперь вы можете войти.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Авторизация"""
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['email'],
                          user['password_hash'], user['is_admin'], user['rating'])
            login_user(user_obj, remember=True)
            flash(f'Добро пожаловать, {username}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    """Выход"""
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))


@app.route('/tasks')
def tasks():
    """Каталог задач"""
    db = get_db()
    
    # Фильтры
    difficulty = request.args.get('difficulty', 'all')
    category = request.args.get('category', 'all')
    
    query = 'SELECT * FROM tasks WHERE 1=1'
    params = []
    
    if difficulty != 'all':
        query += ' AND difficulty = ?'
        params.append(difficulty)
    
    if category != 'all':
        query += ' AND category = ?'
        params.append(category)
    
    query += ' ORDER BY difficulty, id'
    
    tasks_list = db.execute(query, params).fetchall()
    
    # Получаем категории для фильтра
    categories = db.execute('SELECT DISTINCT category FROM tasks').fetchall()
    
    # Прогресс текущего пользователя
    user_progress = {}
    if current_user.is_authenticated:
        progress = db.execute(
            'SELECT task_id, solved FROM user_progress WHERE user_id = ?',
            (current_user.id,)
        ).fetchall()
        user_progress = {p['task_id']: p['solved'] for p in progress}
    
    return render_template('tasks.html', tasks=tasks_list, categories=categories,
                         user_progress=user_progress, difficulty=difficulty, category=category)


@app.route('/task/<int:task_id>', methods=['GET', 'POST'])
@login_required
def task(task_id):
    """Страница задачи"""
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    
    if not task:
        flash('Задача не найдена', 'error')
        return redirect(url_for('tasks'))
    
    # История решений пользователя
    submissions = db.execute('''
        SELECT * FROM submissions 
        WHERE user_id = ? AND task_id = ? 
        ORDER BY submitted_at DESC LIMIT 10
    ''', (current_user.id, task_id)).fetchall()
    
    if request.method == 'POST':
        code = request.form['code']
        
        if not code.strip():
            flash('Код не может быть пустым', 'error')
            return redirect(url_for('task', task_id=task_id))
        
        # Проверяем решение
        status, details = check_solution(task_id, code)
        
        # Сохраняем в БД
        db.execute('''
            INSERT INTO submissions (user_id, task_id, code, status, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (current_user.id, task_id, code, status, details))
        
        # Обновляем прогресс
        progress = db.execute(
            'SELECT * FROM user_progress WHERE user_id = ? AND task_id = ?',
            (current_user.id, task_id)
        ).fetchone()
        
        if progress:
            db.execute('''
                UPDATE user_progress 
                SET attempts = attempts + 1, last_attempt_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND task_id = ?
            ''', (current_user.id, task_id))
        else:
            db.execute('''
                INSERT INTO user_progress (user_id, task_id, attempts, last_attempt_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ''', (current_user.id, task_id))
        
        # Если решено правильно
        if status == 'accepted':
            if not progress or not progress['solved']:
                db.execute('''
                    UPDATE user_progress 
                    SET solved = 1, first_solved_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND task_id = ?
                ''', (current_user.id, task_id))
                
                # Увеличиваем рейтинг
                db.execute(
                    'UPDATE users SET rating = rating + ? WHERE id = ?',
                    (10 * task['difficulty'], current_user.id)
                )
                flash(f'Поздравляем! Задача решена! +{10 * task["difficulty"]} к рейтингу', 'success')
            else:
                flash('Задача решена верно! (уже была решена ранее)', 'success')
        else:
            flash(f'Решение не прошло проверку: {details}', 'error')
        
        db.commit()
        return redirect(url_for('task', task_id=task_id))
    
    # Проверяем решена ли задача
    solved = db.execute(
        'SELECT solved FROM user_progress WHERE user_id = ? AND task_id = ? AND solved = 1',
        (current_user.id, task_id)
    ).fetchone()
    
    return render_template('task.html', task=task, submissions=submissions, solved=solved)


@app.route('/profile')
@login_required
def profile():
    """Профиль пользователя"""
    db = get_db()
    
    # Статистика
    stats = db.execute('''
        SELECT 
            COUNT(DISTINCT task_id) as solved_count,
            SUM(attempts) as total_attempts
        FROM user_progress 
        WHERE user_id = ? AND solved = 1
    ''', (current_user.id,)).fetchone()
    
    # Решенные задачи
    solved_tasks = db.execute('''
        SELECT t.title, t.difficulty, up.first_solved_at
        FROM user_progress up
        JOIN tasks t ON up.task_id = t.id
        WHERE up.user_id = ? AND up.solved = 1
        ORDER BY up.first_solved_at DESC
    ''', (current_user.id,)).fetchall()
    
    # История отправок
    recent_submissions = db.execute('''
        SELECT s.*, t.title as task_title
        FROM submissions s
        JOIN tasks t ON s.task_id = t.id
        WHERE s.user_id = ?
        ORDER BY s.submitted_at DESC LIMIT 20
    ''', (current_user.id,)).fetchall()
    
    return render_template('profile.html', stats=stats, solved_tasks=solved_tasks,
                         submissions=recent_submissions)


# ==================== АДМИНКА ====================

@app.route('/admin')
@admin_required
def admin():
    """Административная панель"""
    db = get_db()
    
    stats = {
        'users': db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count'],
        'tasks': db.execute('SELECT COUNT(*) as count FROM tasks').fetchone()['count'],
        'submissions': db.execute('SELECT COUNT(*) as count FROM submissions').fetchone()['count'],
        'today_submissions': db.execute('''
            SELECT COUNT(*) as count FROM submissions 
            WHERE date(submitted_at) = date('now')
        ''').fetchone()['count']
    }
    
    users = db.execute('''
        SELECT u.*, 
               COUNT(DISTINCT up.task_id) as solved_count,
               COUNT(DISTINCT s.id) as submissions_count
        FROM users u
        LEFT JOIN user_progress up ON u.id = up.user_id AND up.solved = 1
        LEFT JOIN submissions s ON u.id = s.user_id
        GROUP BY u.id
        ORDER BY u.id DESC
    ''').fetchall()
    
    tasks_list = db.execute('SELECT * FROM tasks ORDER BY id DESC').fetchall()
    
    return render_template('admin.html', stats=stats, users=users, tasks=tasks_list)


@app.route('/admin/add_task', methods=['POST'])
@admin_required
def add_task():
    """Добавление новой задачи"""
    db = get_db()
    
    title = request.form['title']
    description = request.form['description']
    difficulty = int(request.form['difficulty'])
    input_format = request.form['input_format']
    output_format = request.form['output_format']
    sample_input = request.form['sample_input']
    sample_output = request.form['sample_output']
    category = request.form['category']
    
    # Парсим тест-кейсы
    tests_input = request.form['tests_input'].strip().split('\n')
    tests_output = request.form['tests_output'].strip().split('\n')
    
    test_cases = []
    for inp, out in zip(tests_input, tests_output):
        if inp.strip() and out.strip():
            test_cases.append({'input': inp.strip(), 'output': out.strip()})
    
    db.execute('''
        INSERT INTO tasks (title, description, difficulty, input_format, output_format,
                          sample_input, sample_output, test_cases, category, author_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (title, description, difficulty, input_format, output_format,
          sample_input, sample_output, json.dumps(test_cases), category, current_user.id))
    
    db.commit()
    flash('Задача успешно добавлена!', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/delete_task/<int:task_id>', methods=['POST'])
@admin_required
def delete_task(task_id):
    """Удаление задачи"""
    db = get_db()
    db.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    db.commit()
    flash('Задача удалена', 'success')
    return redirect(url_for('admin'))


# ==================== PvP СОРЕВНОВАНИЯ ====================

@app.route('/pvp')
@login_required
def pvp():
    """Страница PvP соревнований"""
    db = get_db()
    
    # Активные комнаты
    active_rooms = []
    for room_id, room_data in pvp_rooms.items():
        if room_data['status'] == 'waiting':
            player1 = db.execute('SELECT username FROM users WHERE id = ?', 
                               (room_data['player1_id'],)).fetchone()
            active_rooms.append({
                'id': room_id,
                'player1': player1['username'] if player1 else 'Unknown',
                'task_difficulty': room_data.get('difficulty', 'Любая')
            })
    
    # История матчей
    matches = db.execute('''
        SELECT m.*, 
               p1.username as player1_name,
               p2.username as player2_name,
               w.username as winner_name,
               t.title as task_title
        FROM pvp_matches m
        JOIN users p1 ON m.player1_id = p1.id
        JOIN users p2 ON m.player2_id = p2.id
        LEFT JOIN users w ON m.winner_id = w.id
        JOIN tasks t ON m.task_id = t.id
        WHERE m.player1_id = ? OR m.player2_id = ?
        ORDER BY m.created_at DESC LIMIT 20
    ''', (current_user.id, current_user.id)).fetchall()
    
    return render_template('pvp.html', active_rooms=active_rooms, matches=matches)


@app.route('/pvp/create_room', methods=['POST'])
@login_required
def create_pvp_room():
    """Создание PvP комнаты"""
    import uuid
    
    room_id = str(uuid.uuid4())[:8]
    difficulty = request.form.get('difficulty', 'all')
    
    pvp_rooms[room_id] = {
        'player1_id': current_user.id,
        'player2_id': None,
        'task_id': None,
        'status': 'waiting',
        'difficulty': difficulty,
        'results': {}
    }
    
    return redirect(url_for('pvp_room', room_id=room_id))


@app.route('/pvp/room/<room_id>')
@login_required
def pvp_room(room_id):
    """Комната PvP"""
    if room_id not in pvp_rooms:
        flash('Комната не найдена', 'error')
        return redirect(url_for('pvp'))
    
    room = pvp_rooms[room_id]
    db = get_db()
    
    # Проверяем, является ли пользователь участником
    if room['player1_id'] != current_user.id and room['player2_id'] is None:
        # Присоединяемся как второй игрок
        room['player2_id'] = current_user.id
        room['status'] = 'ready'
    
    if current_user.id not in [room['player1_id'], room['player2_id']]:
        flash('Вы не участник этой комнаты', 'error')
        return redirect(url_for('pvp'))
    
    # Получаем информацию об игроках
    player1 = db.execute('SELECT username, rating FROM users WHERE id = ?', 
                        (room['player1_id'],)).fetchone()
    player2 = db.execute('SELECT username, rating FROM users WHERE id = ?', 
                        (room['player2_id'],)).fetchone() if room['player2_id'] else None
    
    # Выбираем задачу если оба игрока готовы
    task = None
    if room['status'] in ['ready', 'active', 'finished'] and room['task_id']:
        task = db.execute('SELECT * FROM tasks WHERE id = ?', (room['task_id'],)).fetchone()
    
    return render_template('pvp_room.html', room_id=room_id, room=room,
                         player1=player1, player2=player2, task=task)


# ==================== WEBSOCKET ОБРАБОТЧИКИ ====================

@socketio.on('connect')
def handle_connect():
    """Подключение клиента"""
    print(f'Client connected: {request.sid}')


@socketio.on('disconnect')
def handle_disconnect():
    """Отключение клиента"""
    print(f'Client disconnected: {request.sid}')


@socketio.on('join_room')
def on_join(data):
    """Присоединение к комнате"""
    room_id = data['room_id']
    join_room(room_id)
    
    if room_id in pvp_rooms:
        room = pvp_rooms[room_id]
        
        # Отправляем информацию о комнате всем участникам
        emit('room_update', {
            'player1_id': room['player1_id'],
            'player2_id': room['player2_id'],
            'status': room['status']
        }, room=room_id)
        
        # Если оба игрока в комнате, выбираем задачу
        if room['player1_id'] and room['player2_id'] and room['status'] == 'ready':
            db = get_db()
            
            # Выбираем случайную задачу
            if room.get('difficulty') and room['difficulty'] != 'all':
                tasks = db.execute(
                    'SELECT * FROM tasks WHERE difficulty = ? ORDER BY RANDOM() LIMIT 1',
                    (room['difficulty'],)
                ).fetchall()
            else:
                tasks = db.execute('SELECT * FROM tasks ORDER BY RANDOM() LIMIT 1').fetchall()
            
            if tasks:
                room['task_id'] = tasks[0]['id']
                room['status'] = 'active'
                
                emit('game_start', {
                    'task_id': tasks[0]['id'],
                    'task_title': tasks[0]['title'],
                    'task_description': tasks[0]['description'],
                    'input_format': tasks[0]['input_format'],
                    'output_format': tasks[0]['output_format'],
                    'sample_input': tasks[0]['sample_input'],
                    'sample_output': tasks[0]['sample_output']
                }, room=room_id)


@socketio.on('submit_solution')
def handle_pvp_solution(data):
    """Обработка решения в PvP режиме"""
    room_id = data['room_id']
    code = data['code']
    
    if room_id not in pvp_rooms:
        emit('submission_result', {'error': 'Комната не найдена'})
        return
    
    room = pvp_rooms[room_id]
    
    if room['status'] != 'active':
        emit('submission_result', {'error': 'Игра не активна'})
        return
    
    # Проверяем решение
    status, details = check_solution(room['task_id'], code)
    
    # Сохраняем результат
    room['results'][current_user.id] = {
        'status': status,
        'time': datetime.now().isoformat(),
        'code': code
    }
    
    emit('submission_result', {
        'status': status,
        'details': details,
        'player_id': current_user.id
    }, room=room_id)
    
    # Проверяем, есть ли победитель
    if status == 'accepted':
        # Определяем победителя
        winner_id = current_user.id
        loser_id = room['player1_id'] if winner_id == room['player2_id'] else room['player2_id']
        
        room['status'] = 'finished'
        room['winner_id'] = winner_id
        
        # Сохраняем в БД
        db = get_db()
        db.execute('''
            INSERT INTO pvp_matches (player1_id, player2_id, task_id, winner_id, status, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (room['player1_id'], room['player2_id'], room['task_id'], 
              winner_id, 'finished', datetime.now()))
        
        # Обновляем рейтинги
        db.execute('UPDATE users SET rating = rating + 25 WHERE id = ?', (winner_id,))
        db.execute('UPDATE users SET rating = rating - 10 WHERE id = ?', (loser_id,))
        db.commit()
        
        # Отправляем результат
        emit('game_finished', {
            'winner_id': winner_id,
            'winner_username': current_user.username
        }, room=room_id)



@socketio.on('leave_room')
def on_leave(data):
    """Выход из комнаты"""
    room_id = data['room_id']
    leave_room(room_id)
    
    if room_id in pvp_rooms:
        room = pvp_rooms[room_id]
        
        # Если кто-то выходит до окончания игры
        if room['status'] in ['waiting', 'ready', 'active']:
            if current_user.id == room['player1_id']:
                other_player = room['player2_id']
            else:
                other_player = room['player1_id']
            
            if other_player:
                emit('opponent_left', {'message': 'Противник покинул комнату'}, room=room_id)
        
        # Удаляем комнату если она пустая
        if room['player1_id'] == current_user.id:
            room['player1_id'] = None
        if room['player2_id'] == current_user.id:
            room['player2_id'] = None
        
        if room['player1_id'] is None and room['player2_id'] is None:
            del pvp_rooms[room_id]


# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    with app.app_context():
        init_db()
    
    # Получаем порт из переменной окружения (для хостинга) или используем 5000
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    print("=" * 60)
    print("Olympiad Training Platform")
    print("=" * 60)
    print(f"Сервер запущен на http://localhost:{port}")
    print("Администратор: admin / admin123")
    print("=" * 60)
    
    socketio.run(app, host='0.0.0.0', port=port, debug=debug, allow_unsafe_werkzeug=True)
