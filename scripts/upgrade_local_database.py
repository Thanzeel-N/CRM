"""Back up and migrate only a SQLite database inside this checkout."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from app.config import settings


def main():
    url = make_url(settings.database_url)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        raise SystemExit('This helper only migrates a local SQLite database. Back up your server database and use alembic upgrade head there.')
    database = Path(url.database).resolve()
    if not database.is_relative_to(root) or not database.is_file():
        raise SystemExit('Database must be an existing file inside this checkout.')
    backup = database.with_name(database.stem + '.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.db')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    print('Backup:', backup.name)
    config = Config(str(root / 'alembic.ini'))
    command.upgrade(config, 'head')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        print('Migration:', connection.execute('SELECT version_num FROM alembic_version').fetchone()[0])
        print('Organization timezones:', connection.execute('SELECT timezone, COUNT(*) FROM organizations GROUP BY timezone').fetchall())
        print('Lead rows:', connection.execute('SELECT COUNT(*) FROM leads').fetchone()[0])
    print('Google Sheets credentials configured:', bool(settings.google_sheets_credentials_file))


if __name__ == '__main__':
    main()
