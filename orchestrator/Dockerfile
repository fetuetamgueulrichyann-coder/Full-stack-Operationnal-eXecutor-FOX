FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

EXPOSE 5000
# Les migrations s'appliquent avant le démarrage ; un échec arrête le conteneur.
CMD ["sh", "-c", "python migrate.py && python bootstrap.py && exec gunicorn -b 0.0.0.0:5000 -w 2 --timeout 120 app:app"]
