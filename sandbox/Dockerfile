FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir numpy==2.1.3 pandas==2.2.3 scikit-learn==1.5.2 matplotlib==3.9.2
COPY harness.py /opt/harness.py
USER 65534:65534
ENTRYPOINT ["python", "/opt/harness.py"]
