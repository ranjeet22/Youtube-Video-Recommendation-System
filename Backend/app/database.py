from contextlib import contextmanager
import mysql.connector
from mysql.connector import Error
from app import config


def get_connection():
    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
    )


@contextmanager
def db_cursor(dictionary=False):
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)
    try:
        yield connection, cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def ping_database():
    try:
        with db_cursor(dictionary=True) as (_, cursor):
            cursor.execute("SELECT 1 AS ok")
            return cursor.fetchone()["ok"] == 1
    except Error:
        return False
