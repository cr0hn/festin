FROM python:3.8-slim as builder

COPY . /festin
RUN pip wheel --no-cache-dir --wheel-dir=/root/wheels -r /festin/requirements.txt \
    &&  pip wheel --no-cache-dir --wheel-dir=/root/wheels /festin

FROM python:3.8-slim-buster
COPY --from=builder /root/wheels /root/wheels

RUN python -m pip install --no-cache-dir --no-cache /root/wheels/* \
    && rm -rf /root/wheels

ENTRYPOINT ["festin"]
