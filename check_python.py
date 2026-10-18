"""Check the interpreter before importing application dependencies."""
import sys

MINIMUM_PYTHON = (3, 11)


def require_supported_python(version=None):
    current = sys.version_info if version is None else version
    if tuple(current[:2]) < MINIMUM_PYTHON:
        detected = ".".join(str(part) for part in current[:3])
        raise SystemExit(
            "Unsupported Python " + detected + ". Required: Python >=3.11.\n"
            "Ошибка: для Olympiad Platform требуется Python 3.11 или новее.\n"
            "Сейчас используется Python " + detected + ".\n"
            "Выберите Python 3.11+ в настройках интерпретатора PyCharm "
            "и создайте виртуальное окружение на этой версии.\n"
            "Уже установленную старую версию Python удалять не нужно."
        )


if __name__ == "__main__":
    require_supported_python()
    print("Python " + sys.version.split()[0] + " подходит: требуется 3.11 или новее.")
