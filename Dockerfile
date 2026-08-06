FROM python:3.12-slim

WORKDIR /srv

# Zaleznosci najpierw (cache warstw)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kod
COPY app ./app
COPY static ./static

EXPOSE 8000

# Jeden proces w jednym kontenerze (§3). Workers/threads dostroic wg obciazenia.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
