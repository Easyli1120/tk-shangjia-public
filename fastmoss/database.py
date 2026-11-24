"""Release SQLite file handles after each transaction, including on Windows."""
import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *exception):
        try:
            return super().__exit__(*exception)
        finally:
            self.close()
