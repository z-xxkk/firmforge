# FirmForge 判定器工具链镜像（ARCHITECTURE §14）
FROM ubuntu:24.04

# TZ=CST-8 是 POSIX 写法的北京时间（UTC+8），不需要安装 tzdata；运行目录名、日志时间都用它
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUTF8=1 \
    ASAN_OPTIONS=detect_leaks=0 \
    TZ=CST-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gcc libc6-dev gcc-arm-none-eabi libnewlib-arm-none-eabi binutils-arm-none-eabi \
      cppcheck python3 python3-pip git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/firmforge
COPY . /opt/firmforge
RUN pip install --break-system-packages -e .

ENTRYPOINT ["firmforge"]
