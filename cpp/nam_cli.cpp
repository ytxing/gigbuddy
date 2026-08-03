// nam_cli: headless NAM (.nam) renderer built on NeuralAudio (MIT)
// usage: nam_cli <model.nam> <input.wav> <output.wav> [block_size=512]
// Reads a single-channel wav (PCM16/24/32 or float32), runs the model, writes float32 wav.
#include "NeuralModel.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <filesystem>

// ---------------- minimal WAV I/O ----------------
struct WavData {
    uint32_t sampleRate = 0;
    std::vector<float> samples;
};

static bool read_wav(const char* path, WavData& out) {
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return false; }
    char hdr[12];
    if (fread(hdr, 1, 12, f) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) {
        fclose(f); fprintf(stderr, "not a RIFF/WAVE file: %s\n", path); return false;
    }
    uint16_t audioFormat = 0, channels = 0, bits = 0;
    uint32_t sampleRate = 0, dataSize = 0;
    bool haveFmt = false, haveData = false;
    while (!haveData) {
        char ck[8];
        if (fread(ck, 1, 8, f) != 8) break;
        uint32_t ckSize;
        memcpy(&ckSize, ck + 4, 4);
        if (!memcmp(ck, "fmt ", 4)) {
            if (ckSize < 16) { fclose(f); return false; }
            uint16_t fmt[8];
            if (fread(fmt, 1, 16, f) != 16) break;
            audioFormat = fmt[0];
            channels = fmt[1];
            sampleRate = (uint32_t)fmt[2] | ((uint32_t)fmt[3] << 16);
            bits = fmt[7];
            haveFmt = true;
            if (ckSize > 16) fseek(f, ckSize - 16, SEEK_CUR);
        } else if (!memcmp(ck, "data", 4)) {
            dataSize = ckSize; haveData = true;
        } else {
            fseek(f, ckSize + (ckSize & 1), SEEK_CUR);
        }
    }
    if (!haveFmt || !haveData || channels == 0) { fclose(f); return false; }

    std::vector<uint8_t> raw(dataSize);
    if (fread(raw.data(), 1, dataSize, f) != dataSize) { fclose(f); return false; }
    fclose(f);

    const uint32_t frameBytes = channels * (bits / 8);
    const uint32_t nFrames = dataSize / frameBytes;
    out.sampleRate = sampleRate;
    out.samples.reserve(nFrames);

    if (audioFormat == 3 && bits == 32) {           // IEEE float32
        for (uint32_t i = 0; i < nFrames; i++) {
            float v; memcpy(&v, raw.data() + i * frameBytes, 4);
            out.samples.push_back(v);
        }
    } else if (audioFormat == 1) {                  // PCM
        for (uint32_t i = 0; i < nFrames; i++) {
            const uint8_t* p = raw.data() + i * frameBytes;
            if (bits == 16) {
                int16_t v; memcpy(&v, p, 2);
                out.samples.push_back(v / 32768.0f);
            } else if (bits == 24) {
                int32_t v = (p[0] << 8) | (p[1] << 16) | (p[2] << 24);
                out.samples.push_back(v / 8388608.0f);
            } else if (bits == 32) {
                int32_t v; memcpy(&v, p, 4);
                out.samples.push_back(v / 2147483648.0f);
            } else { fclose(f); return false; }
        }
    } else { fprintf(stderr, "unsupported wav format %u\n", audioFormat); return false; }
    return true;
}

static bool write_wav(const char* path, const std::vector<float>& samples, uint32_t sampleRate) {
    FILE* f = fopen(path, "wb");
    if (!f) return false;
    const uint32_t dataSize = (uint32_t)(samples.size() * 4);
    const uint32_t riffSize = 36 + dataSize;
    auto put32 = [&](uint32_t v) { fwrite(&v, 4, 1, f); };
    auto put16 = [&](uint16_t v) { fwrite(&v, 2, 1, f); };
    fwrite("RIFF", 1, 4, f); put32(riffSize); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); put32(16); put16(3); put16(1); put32(sampleRate);
    put32(sampleRate * 4); put16(4); put16(32);
    fwrite("data", 1, 4, f); put32(dataSize);
    for (float s : samples) { float v = s; fwrite(&v, 4, 1, f); }
    fclose(f);
    return true;
}

// ---------------- main ----------------
int main(int argc, char** argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <model.nam> <input.wav> <output.wav> [block_size=512]\n", argv[0]);
        return 1;
    }
    const char* modelPath = argv[1];
    const char* inPath = argv[2];
    const char* outPath = argv[3];
    const int block = argc > 4 ? atoi(argv[4]) : 512;
    if (block <= 0) { fprintf(stderr, "bad block size\n"); return 1; }
    if (!std::filesystem::exists(modelPath)) {
        fprintf(stderr, "model not found: %s\n", modelPath); return 1;
    }

    NeuralAudio::NeuralModelLoader loader;
    loader.SetDefaultMaxAudioBufferSize(block);
    NeuralAudio::NeuralModel* model = nullptr;
    try {
        model = loader.CreateFromFile(modelPath);
    } catch (const std::exception& e) {
        fprintf(stderr, "failed to load model %s: %s\n", modelPath, e.what());
        return 1;
    }
    if (!model) { fprintf(stderr, "failed to load model %s\n", modelPath); return 1; }

    WavData in;
    if (!read_wav(inPath, in) || in.samples.empty()) { fprintf(stderr, "failed to read input wav\n"); return 1; }
    if (in.sampleRate != 48000 && in.sampleRate != 44100 && in.sampleRate != 96000) {
        fprintf(stderr, "warning: unusual sample rate %u\n", in.sampleRate);
    }

    std::vector<float> out(in.samples.size());
    const size_t n = in.samples.size();
    for (size_t i = 0; i < n; i += block) {
        const size_t len = std::min<size_t>(block, n - i);
        float* inPtr = in.samples.data() + i;
        float* outPtr = out.data() + i;
        model->Process(inPtr, outPtr, (int)len);
    }
    delete model;

    if (!write_wav(outPath, out, in.sampleRate)) { fprintf(stderr, "failed to write %s\n", outPath); return 1; }
    fprintf(stderr, "OK %s -> %s (%zu samples @ %u Hz, block=%d)\n", modelPath, outPath, n, in.sampleRate, block);
    return 0;
}
