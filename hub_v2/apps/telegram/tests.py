import unittest

from .services.formatters import format_message


class TelegramFormatterTests(unittest.TestCase):
    def test_fallback_job_failed_message_is_rich_and_structured(self):
        text = format_message(
            "job_failed",
            action_name="Plano de Construcao",
            ga_name="Atenas",
            server_id="s1-br",
            account_name="Conta Principal",
            node_name="node-01",
            exit_code=12,
            error="sem recurso",
            job_id="abc-123",
        )

        self.assertIn("Plano de Construcao falhou", text)
        self.assertIn("Atenas | s1-br | node-01", text)
        self.assertIn("Exit: <code>12</code>", text)
        self.assertIn("Erro: <code>sem recurso</code>", text)
        self.assertIn("Job: <code>abc-123</code>", text)
