import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backup_db import backup_database


class BackupDbTests(unittest.TestCase):
    def test_backup_preserva_conteudo_do_banco(self):
        with TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source.db"
            destination = Path(tempdir) / "backup.db"
            conn = sqlite3.connect(source)
            try:
                conn.execute("CREATE TABLE exemplo (valor TEXT)")
                conn.execute("INSERT INTO exemplo VALUES ('ok')")
                conn.commit()
            finally:
                conn.close()

            result = backup_database(source, destination)

            self.assertEqual(result, destination)
            conn = sqlite3.connect(destination)
            try:
                self.assertEqual(conn.execute("SELECT valor FROM exemplo").fetchone()[0], "ok")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
