# Small non-root image that runs the quakewatch CLI.
#
#   docker build -t agentbench-quakewatch:local .
#   docker run --rm agentbench-quakewatch:local list --window day
#
# The base is pinned to the same python:3.12-slim digest that the local check
# matrix tests against (see local-matrix-images.json).
ARG PYTHON_IMAGE=python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /src

# Hash-pinned build tooling for this stage, and hash-pinned runtime
# dependencies into a pip-less virtualenv that is copied to the final stage.
COPY requirements/build.txt requirements/runtime.txt requirements/
RUN python -m pip install --require-hashes -r requirements/build.txt \
 && python -m venv --without-pip /opt/venv \
 && python -m pip --python /opt/venv/bin/python install --require-hashes -r requirements/runtime.txt

COPY pyproject.toml README.md ./
COPY src/ src/
RUN python -m build --no-isolation --wheel --outdir /dist \
 && python -m pip --python /opt/venv/bin/python install --no-deps --no-index /dist/quakewatch-*.whl \
 && python -m compileall -q /opt/venv

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="quakewatch" \
      org.opencontainers.image.description="Monitor recent earthquakes from USGS and EMSC"

RUN groupadd --gid 10001 quake \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/quake --shell /usr/sbin/nologin quake \
 && mkdir /work \
 && chown quake:quake /work

COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The response cache is written to ./.cache/quakewatch, i.e. under /work.
# Mount a volume there to keep it between runs or to collect `export` output.
WORKDIR /work
USER 10001:10001
ENTRYPOINT ["quakewatch"]
CMD ["--help"]
