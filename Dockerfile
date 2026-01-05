FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY rss_tracker.py .
COPY feeds.json .

# Create directories for data files
RUN mkdir -p /app && chmod 755 /app

CMD ["python", "rss_tracker.py"]
