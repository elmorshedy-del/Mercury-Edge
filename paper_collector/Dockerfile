FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py weather_collector.py omo_collector.py rule_collector.py validation_collector.py diagnostic_sweep.py paper_engine.py paper_engine_hardened.py strategy_engines.py strategy_runtime.py strategy_runtime_hardened.py shadow_lab.py risk_controls.py portfolio_monitor.py unified_engine.py status_reporter.py audit_replay.py audit_daemon.py runner.py stations.py market_calendar.py asos_evidence.py hard_state_proof.py hard_information_domain.py hard_state_accumulator.py hard_state_journal.py bucket_elimination.py dead_no_execution.py dead_no_executor.py madis_omo.py madis_temperature_mapping.py madis_validation.py madis_transport.py settlement_validation.py settlement_audit_domain.py settlement_journal.py settlement_auditor.py information_visibility.py explainability.py failure_events.py failure_event_sweeper.py replay_domain.py replay_hard_state.py raw_journal.py ./
RUN python -m py_compile collector.py weather_collector.py omo_collector.py rule_collector.py validation_collector.py diagnostic_sweep.py paper_engine.py paper_engine_hardened.py strategy_engines.py strategy_runtime.py strategy_runtime_hardened.py shadow_lab.py risk_controls.py portfolio_monitor.py unified_engine.py status_reporter.py audit_replay.py audit_daemon.py runner.py stations.py market_calendar.py asos_evidence.py hard_state_proof.py hard_information_domain.py hard_state_accumulator.py hard_state_journal.py bucket_elimination.py dead_no_execution.py dead_no_executor.py madis_omo.py madis_temperature_mapping.py madis_validation.py madis_transport.py settlement_validation.py settlement_audit_domain.py settlement_journal.py settlement_auditor.py information_visibility.py explainability.py failure_events.py failure_event_sweeper.py replay_domain.py replay_hard_state.py raw_journal.py

CMD ["python", "runner.py"]
