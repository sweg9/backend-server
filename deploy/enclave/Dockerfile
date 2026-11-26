# The Nitro Enclave's image: the enclave program, ai-engine with its YOLO models, and kmstool_enclave_cli
# (wiki: Stack > Deployment on AWS). `nitro-cli build-enclave` turns it into ml-engine.eif; its PCR0 goes
# into the KMS key policy (C-05). The enclave has no disk and no network: everything it needs is in here,
# and the settings come from the server in `init`.
#
# Keep the image under 2 GiB: nitro-cli 1.5's linuxkit fails (E48) on a larger image export. So CPU-only torch
# without its tests, headers and test binaries, headless OpenCV, and no git or pip in the final stage.
#
# Build context (deploy/aws-test/bootstrap.sh makes it on the instance):
#   backend-server/          this repo (src/, pyproject.toml)
#   engine/                  the --engine-root: linker/prior.json and models/*.pt named in ai-engine's settings
#   kmstool_enclave_cli      from aws-nitro-enclaves-sdk-c (bin/kmstool-enclave-cli)
#   libnsm.so                the same build
FROM python:3.11-slim AS build

ARG AI_ENGINE_REV=28be8fb6d74137ea09447573d60973996eb38313
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# CPU-only torch: the PyPI wheel pulls in gigabytes of CUDA libraries the enclave can never use.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 torchvision==0.29.2
RUN pip install "ai-engine[models] @ git+https://github.com/sweg9/ai-engine@${AI_ENGINE_REV}" cryptography
# ultralytics asks for the GUI build of OpenCV; the headless one has the same cv2 without X11 and OpenGL.
RUN v=$(pip show opencv-python | sed -n 's/^Version: //p') \
    && pip uninstall -y opencv-python && pip install "opencv-python-headless==$v"

COPY backend-server/pyproject.toml /build/backend-server/pyproject.toml
COPY backend-server/src /build/backend-server/src
RUN pip install --no-deps /build/backend-server && pip install pydantic pyyaml

RUN cd /usr/local/lib/python3.11/site-packages \
    && rm -rf torch/test torch/include pip setuptools wheel \
    && find torch/bin -type f ! -name torch_shm_manager -delete

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 YOLO_CONFIG_DIR=/tmp/ultralytics YOLO_OFFLINE=1 LD_LIBRARY_PATH=/usr/lib64
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/python3.11/site-packages/pip /usr/local/bin/pip*
COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin/backend /usr/local/bin/backend

COPY engine /app/engine
COPY kmstool_enclave_cli /app/kmstool_enclave_cli
COPY libnsm.so /usr/lib64/libnsm.so
WORKDIR /app

CMD ["backend", "enclave", "--port", "5000", "--engine-root", "/app/engine"]
