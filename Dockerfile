FROM docker.io/astral/uv:python3.13-alpine

WORKDIR /usr

EXPOSE 8770

ENV UV_LINK_MODE=copy

RUN uv pip install --system setuptools wheel

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

ENTRYPOINT [ "uv", "run", "server" ]
