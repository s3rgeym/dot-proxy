FROM python:3.11-alpine

ENV PYTHONUNBUFFERED 1
ENV DNS 1.1.1.1

# Тут доставляет все что может понадобиться
# RUN apk update && apk --no-cache add ... \

WORKDIR /code

COPY dot_proxy.py ./

CMD python dot_proxy.py --host 0.0.0.0 --remote-host "$DNS"
