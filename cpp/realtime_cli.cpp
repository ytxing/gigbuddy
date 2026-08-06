// realtime_cli: 实时吉他音色链（NeuralAudio amp + IR FIR cab），PortAudio(CoreAudio) 低延迟
// usage: realtime_cli <model.nam> [ir.wav] [block=256] [sr=48000]
//        --list            列出输入/输出设备
//        --in NAME         指定输入设备（模糊匹配，如 "Your Device"）
//        --out NAME        指定输出设备
//        --ch N            输入通道号（多输入设备，如 "Your Device": 0=INPUT1 1=INPUT2）
//        --gain X          输入增益（模型前，默认 1.0；>1 推驱动更过载，<1 更清）
//        --master X        输出音量（模型后，默认 1.0；高增益模型压到 0.3 防削波）
//        --live FILE       热切换模式：监听 FILE（JSON: model/ir/gain/master/quality/input），
//                          文件变化时运行时切换，音频不中断；
//                          input 键 = {source:"instrument"|"file", file:<wav>, state:"playing"|"paused"|"stopped", loop:bool}
//                          干声文件输入源（替代乐器输入，试听用）：播放/暂停/停止/循环
//        --level-file FILE 电平输出：0.1s 写 {"in":x,"out":y} 到 FILE（TUI 电平表用）
//        --record-out FILE 旁路录制：实时输出写为 16-bit WAV（无设备验证/录音用）
// 输入: 音频接口（吉他）→ amp(NeuralAudio) → IR(cab) → 输出: 监听设备
#include "NeuralModel.h"
#include "json.hpp"
#include <portaudio.h>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#else
#include <unistd.h>
#endif
#include <fstream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

// Ctrl+C 优雅退出：主循环检查后走正常收尾（录制文件 finalize、流关闭）
static std::atomic<bool> g_stop{false};
static void on_sigint(int) { g_stop.store(true, std::memory_order_relaxed); }

// 前置声明（read_wav_ir 定义在文件后部，make_ir 先用到）
static bool read_wav_ir(const char* path, std::vector<float>& out, uint32_t& sr);

// 简单 FIR（环形历史缓冲，IR 截断至 MAX_IR_TAPS）
class FirFilter {
public:
    void set_ir(const std::vector<float>& ir) {
        taps = ir;
        if (taps.size() > MAX_TAPS) taps.resize(MAX_TAPS);
        hist.assign(taps.size() - 1, 0.0f);
        pos = 0;
    }
    // 逐样本处理：x 入缓冲，输出卷积；无 IR 时直通
    void process(const float* in, float* out, int n) {
        if (taps.empty()) { std::copy(in, in + n, out); return; }
        for (int i = 0; i < n; i++) {
            hist[pos] = in[i];
            float acc = 0.0f;
            int h = pos;
            for (int j = 0; j < (int)taps.size(); j++) {
                acc += hist[h] * taps[j];
                if (--h < 0) h = (int)hist.size() - 1;
            }
            out[i] = acc;
            if (++pos >= (int)hist.size()) pos = 0;
        }
    }
private:
    static constexpr int MAX_TAPS = 4096;
    std::vector<float> taps, hist;
    int pos = 0;
};

// 去咔哒窗半程：热切换时 ~10.6ms 音量下沉（淡出 FADE_HALF + 淡入 FADE_HALF，@48k 每半程 ≈5.3ms）
static constexpr int FADE_HALF = 256;

// 旁路录制输出：把实时处理结果写为 16-bit PCM WAV（--record-out 启用）
// 抽象意图：DSP 链（process_block）与物理输出解耦，本类是可插拔输出端之一
class WavOutput {
public:
    bool open(const std::string& path, int sr);
    void write(const float* mono, int frames);   // 音频线程调用（分块 fwrite，安全）
    void finish();                                // 主线程收尾：回填 WAV header
private:
    FILE* f = nullptr;
    uint32_t nSamples = 0;
    int sampleRate = 48000;
};

// 干声文件输入源（链 input 字段 source=file）：wav → 内存样本 → 按播放状态取帧。
// 与 WavOutput 对称：本类是可插拔输入端之一，替代音频接口（乐器）输入。
// 播放状态机（playState/loop 由主线程原子写，read 回调读）：
//   0=stopped（输出静音、位置归零） 1=playing（推进位置） 2=paused（静音、保留位置）
// 播完（非循环）：输出静音、位置停末尾；主线程检测后自动回落 stopped。
// RAMP 短斜坡（~64 样本）防播放/暂停切换瞬间咔哒。
class WavInput {
public:
    // 加载 wav（取第一声道，复用 read_wav_ir）并线性重采样到引擎采样率；失败返回 false
    bool load(const std::string& path, int targetSr) {
        uint32_t srcSr = 0;
        std::vector<float> raw;
        if (!read_wav_ir(path.c_str(), raw, srcSr)) return false;
        if (srcSr != (uint32_t)targetSr) {
            std::vector<float> rs((size_t)(raw.size() * (double)targetSr / srcSr));
            for (size_t i = 0; i < rs.size(); i++) {
                double t = i * (double)srcSr / targetSr;
                size_t a = (size_t)t, b = a + 1 < raw.size() ? a + 1 : a;
                double f = t - a;
                rs[i] = raw[a] * (1 - f) + raw[b] * f;
            }
            raw = std::move(rs);
        }
        samples = std::move(raw);
        pos.store(0, std::memory_order_relaxed);
        rampPos = 0;
        return true;
    }
    void reset() { pos.store(0, std::memory_order_relaxed); }
    uint64_t size() const { return samples.size(); }
    uint64_t position() const { return pos.load(std::memory_order_relaxed); }
    // 回调线程取帧：state/loop 由调用方原子读后传入；非播放输出静音
    void read(float* out, int frames, int state, bool loop) {
        if (samples.empty()) { std::fill(out, out + frames, 0.0f); return; }
        if (state == 0) { pos.store(0, std::memory_order_relaxed); }
        uint64_t p = pos.load(std::memory_order_relaxed);
        const uint64_t n = samples.size();
        for (int i = 0; i < frames; i++) {
            float v = 0.0f;
            if (state == 1) {
                if (p >= n) {
                    if (loop) p = 0;          // 循环：从头再来
                    else { v = 0.0f; }         // 非循环播完：静音（p 停末尾）
                }
                if (p < n) v = samples[p++];
            }
            // 起止斜坡：播放渐入/停止渐出，防咔哒
            if (state == 1) { if (rampPos < RAMP) rampPos++; }
            else            { if (rampPos > 0) rampPos--; }
            out[i] = v * (float)rampPos / RAMP;
        }
        pos.store(p, std::memory_order_relaxed);
    }
private:
    static constexpr int RAMP = 64;
    std::vector<float> samples;
    std::atomic<uint64_t> pos{0};
    int rampPos = 0;   // 回调线程私有
};

struct Ctx {
    // 实时线程读取的共享状态：shared_ptr + atomic_load/atomic_store 原子交换（libc++ 无 atomic<shared_ptr> 特化）
    std::shared_ptr<NeuralAudio::NeuralModel> model;
    std::shared_ptr<FirFilter> fir;
    std::atomic<float> inputGain{1.0f};   // 输入增益（模型前）
    std::atomic<float> master{1.0f};      // 输出音量（模型后）
    // 去咔哒状态机（fadeState/fadeReady 主线程与回调协作；fadePos 仅回调私有）：
    //   fadeState: 0=无 1=淡出(1→0) 2=淡入(0→1)；主线程只发 1，回调推进
    //   fadeReady: 1=已到静音点（回调置位）——主线程据此等待后再交换，
    //               保证旧输出先被淡出到 0，交换发生在静音处，新输出从 0 淡入
    std::atomic<int> fadeState{0};
    std::atomic<int> fadeReady{0};
    int fadePos = FADE_HALF;
    // 旁路录制端：启动后不变（回调线程只读指针，安全），nullptr 表示不录制
    WavOutput* wavOut = nullptr;
    // 干声文件输入源（链 input.source=file 时替代乐器输入；回调读，主线程交换）
    std::shared_ptr<WavInput> wavIn;
    std::atomic<bool> inIsFile{false};   // true = 输入源为干声文件（忽略音频接口）
    std::atomic<int> playState{0};       // 0=stopped 1=playing 2=paused（回调读）
    std::atomic<bool> loop{false};       // 循环播放
    std::atomic<float> playPosSec{0.0f}; // 播放位置（秒，level 回传给 TUI）
    std::vector<float> scratch;   // 选定通道的单声道缓冲（回调线程私有）
    int inCh = 0;                 // 选定输入通道
    int inChannels = 1;           // 打开的输入通道数
    std::atomic<float> inPeak{0.0f};   // 输入电平（VU 监控）
    std::atomic<float> outPeak{0.0f};  // 输出电平
    // 主线程私有：live 热切换记录（不参与并发）
    std::string liveModelPath, liveIrPath, liveInputPath;
    float liveQuality = 1.0f;   // A2 质量档位（0~1，模型默认 1.0 = Full）
};

// 读 IR 文件 → 重采样/归一 → FirFilter（失败返回 nullptr）
static std::shared_ptr<FirFilter> make_ir(const std::string& path, int sr) {
    std::vector<float> ir; uint32_t irSr = 0;
    if (!read_wav_ir(path.c_str(), ir, irSr)) return nullptr;
    if (irSr != (uint32_t)sr) {   // 线性重采样
        std::vector<float> rs((size_t)(ir.size() * (double)sr / irSr));
        for (size_t i = 0; i < rs.size(); i++) {
            double t = i * (double)irSr / sr;
            size_t a = (size_t)t, b = a + 1 < ir.size() ? a + 1 : a;
            double f = t - a;
            rs[i] = ir[a] * (1 - f) + ir[b] * f;
        }
        ir = std::move(rs);
    }
    double e = 0; for (float v : ir) e += v * v;
    e = sqrt(e); for (float& v : ir) v = e > 1e-9 ? v / (float)e : 0.0f;
    auto f = std::make_shared<FirFilter>();
    f->set_ir(ir);
    return f;
}

// 请求去咔哒并等待静音点：回调淡出旧输出到 0 后置 fadeReady，主线程等它
// 再交换，保证交换发生在静音处（超时 50ms 降级：直接交换，宁可有小咔哒不卡主线程）
static void request_fade_and_wait(Ctx& c) {
    c.fadeReady.store(0, std::memory_order_relaxed);
    c.fadeState.store(1, std::memory_order_relaxed);
    auto start = std::chrono::steady_clock::now();
    while (c.fadeReady.load(std::memory_order_relaxed) == 0) {
        if (std::chrono::steady_clock::now() - start > std::chrono::milliseconds(50))
            break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

// live 热切换：按配置更新 model/ir/gain/master/quality（有变化才重载，加载失败保留旧值）
// REQ-035 portable：模型/IR/live 文件路径相对时按项目根解析。
// 项目根 = --root 参数（TUI/CLI 显式传入）或 exe 所在目录的父目录
// （bin/realtime_cli → 项目根）；引擎从任意 cwd 启动都可靠。
static std::string g_root = ".";

static std::string executable_dir() {
#ifdef __APPLE__
    char buf[4096];
    uint32_t size = sizeof(buf);
    if (_NSGetExecutablePath(buf, &size) == 0) {
        std::string p(buf);
        size_t slash = p.rfind('/');
        return slash == std::string::npos ? "." : p.substr(0, slash);
    }
#else
    char buf[4096];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = 0;
        std::string p(buf);
        size_t slash = p.rfind('/');
        return slash == std::string::npos ? "." : p.substr(0, slash);
    }
#endif
    return ".";
}

static std::string resolve_path(const std::string& p) {
    if (p.empty() || p[0] == '/') return p;   // 绝对路径原样
    return g_root + "/" + p;
}

static void apply_chain(const nlohmann::json& j, NeuralAudio::NeuralModelLoader& loader,
                        Ctx& ctx, int sr) {
    if (j.contains("model")) {
        if (j["model"].is_null()) {
            // AMP bypass：移除模型 → 输入直通（process_block 无模型时透传）
            if (!ctx.liveModelPath.empty()) {
                request_fade_and_wait(ctx);
                std::atomic_store(&ctx.model, std::shared_ptr<NeuralAudio::NeuralModel>());
                ctx.liveModelPath.clear();
                fprintf(stderr, "[live] 模型移除（直通）\n");
            }
        } else if (j["model"].is_string()) {
            std::string p = j["model"].get<std::string>();
            if (p != ctx.liveModelPath) {
                auto nm = std::shared_ptr<NeuralAudio::NeuralModel>(loader.CreateFromFile(resolve_path(p)));
                if (nm) {
                    request_fade_and_wait(ctx);        // 淡出旧输出到静音点
                    std::atomic_store(&ctx.model, nm); // 静音处交换
                    ctx.liveModelPath = p;             // 成功才记录（失败保留旧值可重试）
                    ctx.liveQuality = 1.0f; fprintf(stderr, "[live] 模型 -> %s\n", p.c_str());
                }
                else fprintf(stderr, "[live] 模型加载失败: %s\n", p.c_str());
            }
        }
    }
    if (j.contains("quality")) {
        float q = (float)j["quality"];
        if (q != ctx.liveQuality) {
            ctx.liveQuality = q;
            auto m = std::atomic_load(&ctx.model);
            if (m && m->HasQualityScaling()) {
                m->SetQualityScaleFactor(q);
                fprintf(stderr, "[live] A2 quality -> %.2f\n", q);
            }
        }
    }
    if (j.contains("ir")) {
        std::string p = j["ir"].is_null() ? "" : j["ir"].get<std::string>();
        if (p != ctx.liveIrPath) {
            if (p.empty()) {
                request_fade_and_wait(ctx);  // 淡出旧输出 → 静音点再移除 FIR
                std::atomic_store(&ctx.fir, std::shared_ptr<FirFilter>());
                ctx.liveIrPath = p;
                fprintf(stderr, "[live] IR 移除\n");
            } else {
                // 慢加载（IR 重采样可能几百 ms）在 fade 请求之前，否则淡出窗
                // 早于交换结束 → 突跳无衰减
                auto f = make_ir(resolve_path(p), sr);
                if (f) {
                    request_fade_and_wait(ctx);
                    std::atomic_store(&ctx.fir, f);
                    ctx.liveIrPath = p;
                    fprintf(stderr, "[live] IR -> %s\n", p.c_str());
                }
                else fprintf(stderr, "[live] IR 读取失败: %s\n", p.c_str());
            }
        }
    }
    if (j.contains("gain")) ctx.inputGain.store((float)j["gain"]);
    if (j.contains("master")) ctx.master.store((float)j["master"]);
    // 输入源：input = {source: "instrument"|"file", file, state, loop}
    //   缺失或 source=instrument → 乐器输入（现状）；file 变化才重载 wav（慢操作
    //   在 fade 前完成，同 IR 模式）；source/state/loop 原子切换，不触发链重载
    if (j.contains("input") && j["input"].is_object()) {
        const auto& inp = j["input"];
        if (inp.contains("file") && inp["file"].is_string()) {
            std::string p = inp["file"].get<std::string>();
            if (p != ctx.liveInputPath) {
                auto wi = std::make_shared<WavInput>();
                if (wi->load(resolve_path(p), sr)) {
                    request_fade_and_wait(ctx);
                    std::atomic_store(&ctx.wavIn, wi);
                    ctx.liveInputPath = p;
                    ctx.playState.store(0);
                    ctx.loop.store(false);
                    fprintf(stderr, "[live] 干声输入 -> %s\n", p.c_str());
                }
                else fprintf(stderr, "[live] 干声读取失败: %s\n", p.c_str());
            }
        }
        if (inp.contains("source")) {
            bool isFile = inp["source"].get<std::string>() == "file";
            if (isFile != ctx.inIsFile.load()) {
                request_fade_and_wait(ctx);   // 乐器 ↔ 文件切换：fade 防信号源突变咔哒
                ctx.inIsFile.store(isFile);
                fprintf(stderr, "[live] 输入源 -> %s\n", isFile ? "干声文件" : "乐器");
            }
        }
        if (inp.contains("state")) {
            std::string s = inp["state"].get<std::string>();
            int st = (s == "playing") ? 1 : (s == "paused") ? 2 : 0;
            if (st != ctx.playState.load()) {
                if (st == 1 && ctx.playState.load() == 0 && ctx.wavIn)
                    ctx.wavIn->reset();   // stopped → playing：从头播放
                ctx.playState.store(st);
                fprintf(stderr, "[live] 播放 -> %s\n", s.c_str());
            }
        }
        if (inp.contains("loop")) {
            bool l = inp["loop"].get<bool>();
            if (l != ctx.loop.load()) {
                ctx.loop.store(l);
                fprintf(stderr, "[live] 循环 -> %s\n", l ? "开" : "关");
            }
        }
    }
}

static float block_peak(const float* buf, int n) {
    float p = 0.0f;
    for (int i = 0; i < n; i++) { float a = buf[i] < 0 ? -buf[i] : buf[i]; if (a > p) p = a; }
    return p;
}

// 去咔哒：线性淡出(1→0)→淡入(0→1)。fadePos 回调线程私有；fadeState 主线程只发 1。
// 淡出完成处置 fadeReady（静音点，主线程等它交换）；淡入完成处 CAS——若主线程
// 在淡入期间又发请求（fadeState 被改回 1），CAS 失败则立即重新淡出，不丢更新。
static void apply_fade(float* out, int frames, Ctx& c) {
    int st = c.fadeState.load(std::memory_order_relaxed);
    if (st == 0) return;
    for (int i = 0; i < frames; i++) {
        out[i] *= (float)c.fadePos / FADE_HALF;
        if (st == 1) {
            if (--c.fadePos <= 0) {
                c.fadePos = 0;
                st = 2;
                c.fadeState.store(2, std::memory_order_relaxed);
                c.fadeReady.store(1, std::memory_order_relaxed);  // 静音点：主线程可交换
            }
        } else {
            if (++c.fadePos >= FADE_HALF) {
                c.fadePos = FADE_HALF;
                int expected = 2;
                if (c.fadeState.compare_exchange_strong(expected, 0)) {
                    st = 0;  // 淡入完成，无新请求
                } else {
                    st = 1;  // 主线程已发新请求 → 立即重新淡出
                }
            }
        }
    }
}

// 纯 DSP 链（无 PortAudio 依赖，输出端可插拔——pa_callback 与未来其它输出端共用）：
// mono 输入(scratch) → gain → amp(NeuralAudio) → IR(FIR) → 去咔哒 fade → master → out
static void process_block(const float* in, float* out, int frames, Ctx& c) {
    if (c.inputGain != 1.0f)
        for (int i = 0; i < frames; i++) c.scratch[i] = in[i] * c.inputGain;
    else if (in != c.scratch.data())
        std::copy(in, in + frames, c.scratch.begin());
    c.inPeak.store(block_peak(c.scratch.data(), frames), std::memory_order_relaxed);
    auto m = std::atomic_load(&c.model);
    if (m) m->Process(c.scratch.data(), c.scratch.data(), frames);
    // AMP bypass（model=null）：输入直通，不做任何处理（与 IR 直通对称）
    auto f = std::atomic_load(&c.fir);
    if (f) f->process(c.scratch.data(), out, frames);
    else std::copy(c.scratch.begin(), c.scratch.begin() + frames, out);
    apply_fade(out, frames, c);
    float mstr = c.master.load(std::memory_order_relaxed);
    if (mstr != 1.0f)
        for (int i = 0; i < frames; i++) out[i] *= mstr;
}

static int pa_callback(const void* in, void* out, unsigned long frames,
                       const PaStreamCallbackTimeInfo*, PaStreamCallbackFlags, void* ud) {
    Ctx* c = (Ctx*)ud;
    const float* inBuf = (const float*)in;
    float* outBuf = (float*)out;
    // 多通道输入时解交织，取选定通道；无输入数据时置静音
    const float* mono = nullptr;
    if (c->inIsFile.load(std::memory_order_relaxed)) {
        // 干声文件输入源：替代音频接口，从 wav 缓冲取帧（playState/loop 回调只读）
        auto wi = std::atomic_load(&c->wavIn);
        if (wi) {
            wi->read(c->scratch.data(), (int)frames,
                     c->playState.load(std::memory_order_relaxed),
                     c->loop.load(std::memory_order_relaxed));
            mono = c->scratch.data();
        } else {
            std::fill(c->scratch.begin(), c->scratch.begin() + frames, 0.0f);
            mono = c->scratch.data();
        }
    } else if (!inBuf) {
        std::fill(c->scratch.begin(), c->scratch.begin() + frames, 0.0f);
        mono = c->scratch.data();
    } else if (c->inChannels > 1) {
        for (unsigned long i = 0; i < frames; i++)
            c->scratch[i] = inBuf[i * c->inChannels + c->inCh];
        mono = c->scratch.data();
    } else {
        // 单声道也复制到 scratch（输入增益需要可写缓冲）
        std::copy(inBuf, inBuf + frames, c->scratch.begin());
        mono = c->scratch.data();
    }
    process_block(mono, outBuf, (int)frames, *c);
    if (c->wavOut) c->wavOut->write(outBuf, (int)frames);
    c->outPeak.store(block_peak(outBuf, (int)frames), std::memory_order_relaxed);
    return paContinue;
}

// dBFS 显示（满刻度 1.0 = 0 dBFS，ASCII 电平条 -60..0 dBFS）
static void print_levels(const char* tag, float in, float out) {
    auto db = [](float v) { return v < 1e-5f ? -99.0f : 20.0f * log10f(v); };
    auto bar = [db](float v, int w = 24) {
        float pct = v < 1e-5f ? 0.0f : (db(v) + 60.0f) / 60.0f;
        if (pct < 0.0f) pct = 0.0f;
        if (pct > 1.0f) pct = 1.0f;
        int n = (int)(pct * w);
        std::string s(n, '#');
        s.append(w - n, '.');
        return s;
    };
    // \r + ANSI 清行：原地刷新，不滚动
    fprintf(stderr, "\r\x1b[K[%s] in [%s] %5.1f dBFS  out [%s] %5.1f dBFS",
            tag, bar(in).c_str(), db(in), bar(out).c_str(), db(out));
    fflush(stderr);
}

// ---- WavOutput 实现（旁路录制，16-bit PCM）----
bool WavOutput::open(const std::string& path, int sr) {
    f = fopen(path.c_str(), "wb");
    if (!f) return false;
    nSamples = 0;
    sampleRate = sr;
    // RIFF header 占位（dataSize 在 finish 时回填）
    uint32_t dummy = 0;
    fwrite("RIFF", 1, 4, f); fwrite(&dummy, 4, 1, f);
    fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f);
    uint32_t fmtSize = 16; fwrite(&fmtSize, 4, 1, f);
    uint16_t tag = 1, ch = 1, bits = 16;
    fwrite(&tag, 2, 1, f); fwrite(&ch, 2, 1, f);
    fwrite(&sr, 4, 1, f);
    uint32_t byteRate = (uint32_t)sr * 2; fwrite(&byteRate, 4, 1, f);
    uint16_t blockAlign = 2; fwrite(&blockAlign, 2, 1, f);
    fwrite(&bits, 2, 1, f);
    fwrite("data", 1, 4, f); fwrite(&dummy, 4, 1, f);
    return true;
}

void WavOutput::write(const float* mono, int frames) {
    if (!f) return;
    // 16-bit 转换后分块写：减少音频回调内的 fwrite 调用（stdio 缓冲 + 块级写入）
    constexpr int BLOCK = 256;
    int16_t buf[BLOCK];
    for (int i = 0; i < frames; ) {
        int n = frames - i < BLOCK ? frames - i : BLOCK;
        for (int j = 0; j < n; j++) {
            float v = mono[i + j];
            if (v > 1.0f) v = 1.0f; else if (v < -1.0f) v = -1.0f;
            buf[j] = (int16_t)(v * 32767.0f);
        }
        fwrite(buf, 2, (size_t)n, f);
        i += n;
        nSamples += (uint32_t)n;
    }
}

void WavOutput::finish() {
    if (!f) return;
    uint32_t dataBytes = nSamples * 2;
    fseek(f, 4, SEEK_SET);
    uint32_t chunkSize = dataBytes + 36;       // RIFF chunkSize = data + 36
    fwrite(&chunkSize, 4, 1, f);
    fseek(f, 40, SEEK_SET);
    fwrite(&dataBytes, 4, 1, f);               // dataSize
    fclose(f);
    f = nullptr;
    fprintf(stderr, "[record] 已保存 %u 帧（%.1f 秒）到录制文件\n",
            nSamples, (double)nSamples / sampleRate);
}

// ---- 最小 WAV 读取（复用 nam_cli 逻辑，IR 用）----
static bool read_wav_ir(const char* path, std::vector<float>& out, uint32_t& sr) {
    FILE* f = fopen(path, "rb");
    if (!f) return false;
    char hdr[12];
    if (fread(hdr, 1, 12, f) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) { fclose(f); return false; }
    uint16_t fmtTag = 0, channels = 0, bits = 0;
    uint32_t sampleRate = 0, dataSize = 0;
    bool haveFmt = false, haveData = false;
    while (!haveData) {
        char ck[8];
        if (fread(ck, 1, 8, f) != 8) break;
        uint32_t ckSize; memcpy(&ckSize, ck + 4, 4);
        if (!memcmp(ck, "fmt ", 4)) {
            uint16_t fmt[8];
            if (fread(fmt, 1, 16, f) != 16) break;
            fmtTag = fmt[0]; channels = fmt[1];
            sampleRate = (uint32_t)fmt[2] | ((uint32_t)fmt[3] << 16);
            bits = fmt[7]; haveFmt = true;
            if (ckSize > 16) fseek(f, ckSize - 16, SEEK_CUR);
        } else if (!memcmp(ck, "data", 4)) {
            dataSize = ckSize; haveData = true;
        } else {
            fseek(f, ckSize + (ckSize & 1), SEEK_CUR);
        }
    }
    if (!haveFmt || !haveData) { fclose(f); return false; }
    std::vector<uint8_t> raw(dataSize);
    if (fread(raw.data(), 1, dataSize, f) != dataSize) { fclose(f); return false; }
    fclose(f);
    const uint32_t frameBytes = channels * (bits / 8);
    const uint32_t n = dataSize / frameBytes;
    out.clear(); out.reserve(n);
    if (fmtTag == 3 && bits == 32) {
        for (uint32_t i = 0; i < n; i++) { float v; memcpy(&v, raw.data() + i * frameBytes, 4); out.push_back(v); }
    } else if (fmtTag == 1) {
        for (uint32_t i = 0; i < n; i++) {
            const uint8_t* p = raw.data() + i * frameBytes;
            if (bits == 16) { int16_t v; memcpy(&v, p, 2); out.push_back(v / 32768.0f); }
            else if (bits == 24) {
                // little-endian 24-bit: LSB..MSB with sign in p[2]'s top bit;
                // the old shift pattern (p0<<8|p1<<16|p2<<24) read the value
                // 256x too large, slamming every dry input / 24-bit IR into
                // full-scale clipping.
                int32_t v = (p[0]) | (p[1] << 8) | (p[2] << 16);
                if (v & 0x800000) v |= 0xFF000000;   // sign-extend to 32 bit
                out.push_back(v / 8388608.0f);
            }
            else if (bits == 32) { int32_t v; memcpy(&v, p, 4); out.push_back(v / 2147483648.0f); }
        }
    } else { return false; }
    sr = sampleRate;
    return true;
}

static int find_device(int maxCount, bool wantInput, const char* name) {
    for (int i = 0; i < maxCount; i++) {
        const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
        if (wantInput && info->maxInputChannels == 0) continue;
        if (!wantInput && info->maxOutputChannels == 0) continue;
        if (strstr(info->name, name) != nullptr) return i;
    }
    return -1;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <model.nam> [ir.wav] [block=256] [sr=48000]\n", argv[0]);
        fprintf(stderr, "      --list | --in NAME --out NAME\n");
        return 1;
    }
    // 参数解析：argv[1]=model（或 --list），argv[2]=ir（可选），其后 flags 与数字（block/sr）
    const char* inName = nullptr;
    const char* outName = nullptr;
    bool listOnly = false;
    int inCh = 0;
    float gainArg = 1.0f, masterArg = 1.0f;
    const char* livePath = nullptr;
    const char* levelFile = nullptr;
    const char* recordOut = nullptr;
    if (argc >= 2 && !strcmp(argv[1], "--list")) listOnly = true;
    const char* modelPath = argv[1];
    // argv[2] 是 IR 路径；以 '-' 开头则视为 flag（无 IR 时 --in 等不会误判）
    const char* irPath = (argc > 2 && argv[2][0] != '-') ? argv[2] : nullptr;
    int block = 256, sr = 48000;
    // 从 1 开始：model 可能是位置参数（argv[1]）也可能是 flag（--live 模式）；位置参数非 flag 自然跳过
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--in")) { inName = argv[++i]; }
        else if (!strcmp(argv[i], "--out")) { outName = argv[++i]; }
        else if (!strcmp(argv[i], "--ch")) { inCh = atoi(argv[++i]); }
        else if (!strcmp(argv[i], "--gain")) { gainArg = (float)atof(argv[++i]); }
        else if (!strcmp(argv[i], "--master")) { masterArg = (float)atof(argv[++i]); }
        else if (!strcmp(argv[i], "--live")) { livePath = argv[++i]; }
        else if (!strcmp(argv[i], "--root")) { g_root = argv[++i]; }
        else if (!strcmp(argv[i], "--level-file")) { levelFile = argv[++i]; }
        else if (!strcmp(argv[i], "--record-out")) { recordOut = argv[++i]; }
        else if (!strcmp(argv[i], "--list")) { listOnly = true; }
        else if (!strcmp(argv[i], "--block")) { block = atoi(argv[++i]); }
        else if (!strcmp(argv[i], "--sr")) { sr = atoi(argv[++i]); }
        else if (argv[i][0] >= '0' && argv[i][0] <= '9') {
            if (block == 256) block = atoi(argv[i]);
            else sr = atoi(argv[i]);
        }
    }
    PaStream* stream = nullptr;
    PaError err = Pa_Initialize();
    if (err != paNoError) { fprintf(stderr, "PortAudio 初始化失败: %s\n", Pa_GetErrorText(err)); return 1; }
    const int devCount = Pa_GetDeviceCount();
    if (listOnly) {
        for (int i = 0; i < devCount; i++) {
            const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
            printf("[%d] %s (in=%d out=%d sr=%.0f) %s\n", i, info->name,
                   info->maxInputChannels, info->maxOutputChannels, info->defaultSampleRate,
                   i == Pa_GetDefaultInputDevice() ? "[默认输入]" :
                   i == Pa_GetDefaultOutputDevice() ? "[默认输出]" : "");
        }
        Pa_Terminate();
        return 0;
    }

    NeuralAudio::NeuralModelLoader loader;
    loader.SetDefaultMaxAudioBufferSize(block);

    Ctx ctx;
    ctx.scratch.assign(block, 0.0f);
    ctx.inputGain.store(gainArg);
    ctx.master.store(masterArg);

    // 统一错误退出：任何失败路径都 finalize 录制文件、关 stream、Terminate
    auto fail = [&](const char* msg, const char* detail = "") -> int {
        fprintf(stderr, "%s%s%s\n", msg, detail[0] ? ": " : "", detail);
        if (stream) { Pa_StopStream(stream); Pa_CloseStream(stream); }
        Pa_Terminate();
        if (ctx.wavOut) { ctx.wavOut->finish(); delete ctx.wavOut; }
        return 1;
    };

    // 旁路录制输出（可选）：启动后回调线程只读 wavOut 指针
    if (recordOut) {
        auto w = std::make_unique<WavOutput>();
        if (!w->open(recordOut, sr)) return fail("录制文件打开失败", recordOut);
        ctx.wavOut = w.release();
        fprintf(stderr, "旁路录制: %s\n", recordOut);
    }

    // 初始模型/IR：live 模式以配置文件为准，否则用命令行参数
    if (livePath) {
        try {
            apply_chain(nlohmann::json::parse(std::ifstream(resolve_path(livePath))), loader, ctx, sr);
        } catch (const std::exception& e) {
            return fail("live 配置解析失败", e.what());
        }
        // 空链仍拒绝（静默退出），但 AMP bypass（model=null）+ 干声文件输入合法
        if (!std::atomic_load(&ctx.model) && !ctx.inIsFile.load())
            return fail("live 配置缺少有效 model 字段");
    } else {
        auto model = std::shared_ptr<NeuralAudio::NeuralModel>(loader.CreateFromFile(resolve_path(modelPath)));
        if (!model) return fail("模型加载失败", modelPath);
        std::atomic_store(&ctx.model, model);
        if (irPath) {
            auto f = make_ir(resolve_path(irPath), sr);
            if (f) { std::atomic_store(&ctx.fir, f); fprintf(stderr, "IR 已加载: %s\n", irPath); }
            else fprintf(stderr, "IR 读取失败，仅 amp\n");
        }
    }

    // 选择输入/输出设备
    int inDev = Pa_GetDefaultInputDevice();
    int outDev = Pa_GetDefaultOutputDevice();
    if (inName) {
        inDev = find_device(devCount, true, inName);
        if (inDev < 0) return fail("找不到输入设备（--list 查看）", inName);
    }
    if (outName) {
        outDev = find_device(devCount, false, outName);
        if (outDev < 0) return fail("找不到输出设备（--list 查看）", outName);
    }
    if (inDev < 0 || outDev < 0) return fail("无可用输入/输出设备（--list 查看）");

    PaStreamParameters inParams, outParams;
    memset(&inParams, 0, sizeof(inParams));
    memset(&outParams, 0, sizeof(outParams));
    inParams.device = inDev;
    // 打开全部输入通道（如 2 通道设备），回调里按 --ch 选通道
    ctx.inChannels = Pa_GetDeviceInfo(inDev)->maxInputChannels;
    if (ctx.inChannels > 2) ctx.inChannels = 2;
    ctx.inCh = inCh < ctx.inChannels ? inCh : 0;
    inParams.channelCount = ctx.inChannels;
    inParams.sampleFormat = paFloat32;
    inParams.suggestedLatency = Pa_GetDeviceInfo(inDev)->defaultLowInputLatency;
    outParams.device = outDev;
    outParams.channelCount = 1;
    outParams.sampleFormat = paFloat32;
    outParams.suggestedLatency = Pa_GetDeviceInfo(outDev)->defaultLowOutputLatency;

    err = Pa_OpenStream(&stream, &inParams, &outParams, sr, block, paNoFlag, pa_callback, &ctx);
    if (err != paNoError)
        return fail("打开设备失败（检查麦克风权限/设备占用）", Pa_GetErrorText(err));
    err = Pa_StartStream(stream);
    if (err != paNoError)
        return fail("启动流失败", Pa_GetErrorText(err));
    fprintf(stderr, "实时运行中: %s @ %dHz, block=%d, 理论延迟≈%.1fms — Ctrl+C 退出\n",
            modelPath, sr, block, block * 1000.0 / sr);
    fprintf(stderr, "输入: %s  →  输出: %s\n",
            Pa_GetDeviceInfo(inDev)->name, Pa_GetDeviceInfo(outDev)->name);
    // 实际流延迟（PortAudio/CoreAudio 报告的缓冲延迟，含硬件，≈端到端感知延迟）
    const PaStreamInfo* si = Pa_GetStreamInfo(stream);
    if (si) {
        fprintf(stderr, "实测流延迟: input=%.2fms + output=%.2fms = 总≈%.2fms "
                "（另加弹奏→听感的声学感知 ≈ 5-15ms 可感知）\n",
                si->inputLatency * 1000.0, si->outputLatency * 1000.0,
                (si->inputLatency + si->outputLatency) * 1000.0);
    }
    fprintf(stderr, "提示: 弹奏时注意输入电平；监听建议走声卡耳机口（非蓝牙）\n");
    if (livePath) fprintf(stderr, "live 热切换模式: 监听 %s（改文件即时生效）\n", livePath);
    fprintf(stderr, "电平监控（0.1 秒刷新，Ctrl+C 退出）:\n");
    signal(SIGINT, on_sigint);
    std::filesystem::file_time_type lastWrite{};
    for (;;) {
        if (g_stop.load(std::memory_order_relaxed)) break;
        Pa_Sleep(100);
        if (livePath) {
            std::error_code ec;
            auto t = std::filesystem::last_write_time(livePath, ec);
            if (!ec && t != lastWrite) {
                lastWrite = t;
                try {
                    apply_chain(nlohmann::json::parse(std::ifstream(resolve_path(livePath))), loader, ctx, sr);
                } catch (...) { fprintf(stderr, "[live] 配置解析失败\n"); }
            }
        }
        const float inL = ctx.inPeak.load(std::memory_order_relaxed);
        const float outL = ctx.outPeak.load(std::memory_order_relaxed);
        print_levels("VU", inL, outL);
        // 干声回放：播完检测（非循环）→ 自动停止归零；位置秒数回传 TUI
        if (ctx.inIsFile.load(std::memory_order_relaxed) && ctx.wavIn) {
            if (ctx.playState.load() == 1 && !ctx.loop.load() &&
                ctx.wavIn->position() >= ctx.wavIn->size()) {
                ctx.playState.store(0);
                ctx.wavIn->reset();
                fprintf(stderr, "[live] 干声播完\n");
            }
            ctx.playPosSec.store((float)ctx.wavIn->position() / sr,
                                 std::memory_order_relaxed);
        } else {
            ctx.playPosSec.store(0.0f, std::memory_order_relaxed);
        }
        if (levelFile) {   // 电平写文件（临时文件+rename 原子写，TUI 电平表数据源）
            char tmp[1024];
            snprintf(tmp, sizeof(tmp), "%s.tmp", levelFile);
            FILE* lf = fopen(tmp, "w");
            if (lf) {
                const char* ps = ctx.playState.load() == 1 ? "playing"
                               : ctx.playState.load() == 2 ? "paused" : "stopped";
                fprintf(lf, "{\"in\":%.6f,\"out\":%.6f,\"play_state\":\"%s\",\"play_pos\":%.3f}\n",
                        inL, outL, ps, ctx.playPosSec.load());
                fclose(lf);
                rename(tmp, levelFile);
            }
        }
        // 静音块也清零电平显示
        ctx.inPeak.store(0.0f, std::memory_order_relaxed);
        ctx.outPeak.store(0.0f, std::memory_order_relaxed);
    }
    // 优雅退出：先停流（回调停止后安全 finalize 录制文件），再释放资源
    Pa_StopStream(stream);
    Pa_CloseStream(stream);
    Pa_Terminate();
    if (ctx.wavOut) {
        ctx.wavOut->finish();
        delete ctx.wavOut;
    }
    fprintf(stderr, "\n已退出。\n");
    return 0;
}
