import os
import unittest
from unittest.mock import patch

from backend.app.main import startup


class StartupTests(unittest.TestCase):
    def test_existing_database_mode_checks_connection_without_ddl(self):
        with patch.dict(os.environ, {'DB_INIT_ON_STARTUP': 'false'}), patch('backend.app.main.init_db') as init, patch('backend.app.main.SessionLocal') as session:
            startup()
            init.assert_not_called()
            query = session.return_value.__enter__.return_value.execute.call_args.args[0]
            self.assertEqual(str(query), 'SELECT 1')

    def test_explicit_bootstrap_retains_initialization(self):
        with patch.dict(os.environ, {'DB_INIT_ON_STARTUP': 'true'}), patch('backend.app.main.init_db') as init:
            startup()
            init.assert_called_once_with()
