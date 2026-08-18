FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py weather_collector.py omo_collector.py rule_collector.py audit_replay.py audit_daemon.py runner.py stations.py ./
RUN python -m py_compile collector.py weather_collector.py omo_collector.py rule_collector.py audit_replay.py audit_daemon.py runner.py stations.py

CMD ["python", "runner.py"]
