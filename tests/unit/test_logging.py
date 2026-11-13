"""Contexto da saga nos logs (encaminhados ao New Relic)."""

import contextvars
import logging


class TestContextRecordFactory:
    """saga_id/os_id/correlation_id viram atributos do LogRecord (New Relic context_data)."""

    @staticmethod
    def _novo_record():
        from app.core.logging import _context_record_factory

        return _context_record_factory("t", logging.INFO, "t.py", 1, "msg", (), None)

    def test_copia_o_contexto_para_o_record(self):
        from app.core.logging import correlation_id_ctx, os_id_ctx, saga_id_ctx

        def run():
            saga_id_ctx.set("saga-1")
            os_id_ctx.set(7)
            correlation_id_ctx.set("corr-1")
            return self._novo_record()

        record = contextvars.Context().run(run)
        assert (record.saga_id, record.os_id, record.correlation_id) == (
            "saga-1",
            7,
            "corr-1",
        )

    def test_sem_contexto_nao_cria_atributos(self):
        record = contextvars.Context().run(self._novo_record)
        assert not hasattr(record, "saga_id")
        assert not hasattr(record, "os_id")

    def test_setup_instala_a_factory(self):
        from app.core.logging import _context_record_factory, setup_json_logging

        anterior = logging.getLogRecordFactory()
        try:
            setup_json_logging(service_name="teste", environment="test")
            assert logging.getLogRecordFactory() is _context_record_factory
        finally:
            logging.setLogRecordFactory(anterior)
