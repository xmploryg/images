FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    ffmpeg \
    libc6-dev \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    libexpat1-dev \
    python3 \
    python3-pip \
    libssl-dev \
    libwxbase3.0-dev \
    libwxgtk3.0-gtk3-dev \
    libgl1-mesa-dev \
    mkvtoolnix \
    qtbase5-dev \
    pkg-config \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-spa \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-ita \
    wget \
    zlib1g-dev \
    less \
    && rm -rf /var/lib/apt/lists/*

ENV MAKEMKV_VERSION=1.18.3
ENV RIP_MIN_LENGTH=1200
ENV FEATURE_MIN_SECONDS=2400
ENV SUBTITLE_CLONE_POLICY=never
ENV SUBTITLE_CLONE_LANGS=zho,chi,cmn,yue,jpn,kor,spa,fre,fra,deu,ger,ita,und

WORKDIR /tmp

RUN pip3 install --no-cache-dir deep-translator ffsubsync pgsrip && \
    wget https://www.makemkv.com/download/makemkv-oss-${MAKEMKV_VERSION}.tar.gz && \
    wget https://www.makemkv.com/download/makemkv-bin-${MAKEMKV_VERSION}.tar.gz && \
    tar xzf makemkv-oss-${MAKEMKV_VERSION}.tar.gz && \
    cd makemkv-oss-${MAKEMKV_VERSION} && \
    ./configure --prefix=/usr || { cat config.log; exit 1; } && \
    make -j$(nproc) || { echo "BUILD FAILED"; tail -n 40 config.log || true; exit 1; } && \
    make install && \
    cd /tmp && \
    tar xzf makemkv-bin-${MAKEMKV_VERSION}.tar.gz && \
    cd makemkv-bin-${MAKEMKV_VERSION} && \
    mkdir -p tmp && \
    echo "accepted" > tmp/eula_accepted && \
    make -j$(nproc) && make install

COPY rip_media.py /usr/local/bin/rip-media
COPY ingest_media.py /usr/local/bin/ingest-media
COPY subtitle_workflow.py /usr/local/bin/subtitle-workflow
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x \
    /usr/local/bin/rip-media \
    /usr/local/bin/ingest-media \
    /usr/local/bin/subtitle-workflow \
    /usr/local/bin/entrypoint.sh

WORKDIR /data

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["scan", "/data"]