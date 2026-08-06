// realtime_cli: 实时吉他音色链（0-6 NAM/IR Slots），PortAudio(CoreAudio) 低延迟
// usage: realtime_cli <model.nam> [ir.wav] [block=256] [sr=48000]
//        --list            列出输入/输出设备
//        --in NAME         指定输入设备（模糊匹配，如 "Your Device"）
//        --out NAME        指定输出设备
//        --ch N            输入通道号（多输入设备，如 "Your Device": 0=INPUT1 1=INPUT2）
//        --gain X          输入增益（模型前，默认 1.0；>1 推驱动更过载，<1 更清）
//        --master X        输出音量（模型后，默认 1.0；高增益模型压到 0.3 防削波）
//        --live FILE       热切换模式：监听 FILE（JSON: slots/gain/master/quality/input），
//                          文件变化时运行时切换，音频不中断；
//        --managed --control-file FILE
//                          managed TUI 的候选链 preflight 控制通道；
//                          input 键 = {source:"instrument"|"file", file:<wav>, state:"playing"|"paused"|"stopped", loop:bool}
//                          干声文件输入源（替代乐器输入，试听用）：播放/暂停/停止/循环
//        --level-file FILE 电平输出：0.1s 写 {"in":x,"out":y} 到 FILE（TUI 电平表用）
//        --record-out FILE 旁路录制：实时输出写为 16-bit WAV（无设备验证/录音用）
// 输入: 音频接口（吉他）→ gain → ordered Slots → master → 输出: 监听设备
#include "NeuralModel.h"
#include "json.hpp"
#include <portaudio.h>
#include <atomic>
#include <cmath>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#else
#include <unistd.h>
#endif
#include <fstream>
#include <iterator>
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
        // Keep one history cell per tap.  A one-tap IR is valid and must not
        // index an empty ring; tap zero also remains sample-aligned.
        hist.assign(taps.size(), 0.0f);
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
        if (raw.empty() || srcSr == 0 || targetSr <= 0) return false;
        if (srcSr != (uint32_t)targetSr) {
            std::vector<float> rs((size_t)(raw.size() * (double)targetSr / srcSr));
            if (rs.empty()) return false;
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

enum class NodeKind { Nam, Ir };

// A prepared chain is immutable after publication. The model/FIR objects are
// stateful, but only the audio callback processes them. The main thread builds
// a complete replacement before the shared pointer is exchanged.
struct ChainNode {
    NodeKind kind;
    std::shared_ptr<NeuralAudio::NeuralModel> nam;
    std::shared_ptr<FirFilter> ir;
    std::string path;
};

struct PreparedChain {
    std::vector<ChainNode> nodes;
    std::vector<std::string> signature;
    float gain = 1.0f;
    float master = 1.0f;
    bool mute = false;
    float quality = 1.0f;
    uint64_t revision = 0;
};

struct Ctx {
    // shared_ptr + atomic_load/atomic_store gives a complete chain swap.
    std::shared_ptr<PreparedChain> chain;
    std::atomic<uint64_t> runtimeRevision{0};
    // Main-thread-only status consumed by the level telemetry writer.
    std::string runtimeStatus = "unknown";
    std::string runtimeSessionId;
    std::string runtimeTransactionId;
    uint64_t runtimeAckSequence = 0;
    // Main-thread-only candidate retained from managed preflight. The live
    // watcher consumes it when the matching transaction reaches the file.
    std::shared_ptr<PreparedChain> managedPreparedChain;
    std::string managedPreparedTransactionId;
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
    std::string liveInputPath;
};

// 读 IR 文件 → 重采样/归一 → FirFilter（失败返回 nullptr）
static std::shared_ptr<FirFilter> make_ir(const std::string& path, int sr) {
    std::vector<float> ir; uint32_t irSr = 0;
    if (!read_wav_ir(path.c_str(), ir, irSr)) return nullptr;
    if (ir.empty() || irSr == 0 || sr <= 0) return nullptr;
    if (irSr != (uint32_t)sr) {   // 线性重采样
        std::vector<float> rs((size_t)(ir.size() * (double)sr / irSr));
        if (rs.empty()) return nullptr;
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

// live 热切换：按配置更新完整 Slot chain 和全局参数（有变化才重载，加载失败保留旧值）
// REQ-035 portable：模型/IR/live 文件路径相对时按项目根解析。
// 项目根 = --root 参数（TUI/CLI 显式传入）或 exe 所在目录的父目录
// （bin/realtime_cli → 项目根）；引擎从任意 cwd 启动都可靠。
static std::string g_root = ".";
static bool g_rootExplicit = false;

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

struct SlotSpec {
    bool empty = true;
    NodeKind kind = NodeKind::Nam;
    std::string path;
};

struct InputSpec {
    bool isFile = false;
    std::string path;
    int state = 0;       // stopped=0, playing=1, paused=2
    bool loop = false;
};

static std::string lower_ascii(std::string value) {
    for (char& ch : value) {
        if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
    }
    return value;
}

static bool has_parent_component(const std::filesystem::path& path) {
    for (const auto& part : path) {
        if (part == "..") return true;
    }
    return false;
}

static std::filesystem::path absolute_root() {
    std::error_code ec;
    auto root = std::filesystem::absolute(std::filesystem::path(g_root), ec);
    return (ec ? std::filesystem::path(g_root) : root).lexically_normal();
}

static bool path_is_within(const std::filesystem::path& candidate,
                           const std::filesystem::path& root) {
    std::error_code ec;
    const auto canonicalCandidate = std::filesystem::weakly_canonical(candidate, ec);
    if (ec) return false;
    ec.clear();
    const auto canonicalRoot = std::filesystem::weakly_canonical(root, ec);
    if (ec) return false;
    auto candidateIt = canonicalCandidate.begin();
    for (auto rootIt = canonicalRoot.begin(); rootIt != canonicalRoot.end(); ++rootIt) {
        if (candidateIt == canonicalCandidate.end() || *candidateIt != *rootIt)
            return false;
        ++candidateIt;
    }
    return true;
}

// Resolve a live-protocol path and enforce the protocol's allowed root. The
// external CLI path mode deliberately skips this restriction for v0.1 users.
static bool resolve_asset_path(const std::string& raw, const char* allowedDir,
                               bool allowExternal, bool requireRegular,
                               std::string& resolved, std::string& error) {
    if (raw.empty()) {
        error = "empty path";
        return false;
    }
    const std::filesystem::path rawPath(raw);
    if (!allowExternal && !rawPath.is_absolute() && has_parent_component(rawPath)) {
        error = "path traversal is not allowed";
        return false;
    }
    const auto root = absolute_root();
    const auto candidate = rawPath.is_absolute() ? rawPath : root / rawPath;
    std::error_code ec;
    const auto canonical = std::filesystem::weakly_canonical(candidate, ec);
    if (ec) {
        error = "path cannot be resolved";
        return false;
    }
    if (!allowExternal && !path_is_within(canonical, root / allowedDir)) {
        error = "path is outside the allowed project directory";
        return false;
    }
    if (requireRegular && !std::filesystem::is_regular_file(canonical, ec)) {
        error = "path is not a regular file";
        return false;
    }
    if (!requireRegular && !allowExternal) {
        const bool exists = std::filesystem::exists(canonical, ec);
        if (ec || (exists && !std::filesystem::is_regular_file(canonical, ec))) {
            error = "path is not a regular file";
            return false;
        }
    }
    resolved = canonical.string();
    return true;
}

static bool parse_slot_specs(const nlohmann::json& j, std::vector<SlotSpec>& slots,
                             std::string& error) {
    if (j.contains("slots")) {
        if (j.contains("model") || j.contains("ir")) {
            fprintf(stderr, "[live] warning: slots take precedence over legacy model/ir\n");
        }
        if (!j.at("slots").is_array()) {
            error = "slots must be an array";
            return false;
        }
        if (j.at("slots").size() > 6) {
            error = "slot limit is 6";
            return false;
        }
        for (const auto& item : j.at("slots")) {
            SlotSpec spec;
            if (!item.is_object()) {
                error = "slot must be an object";
                return false;
            }
            if (!item.contains("path")) {
                error = "slot.path is required";
                return false;
            }
            if (item.at("path").is_null()) {
                slots.push_back(std::move(spec));
                continue;
            }
            if (!item.at("path").is_string()) {
                error = "slot.path must be a string or null";
                return false;
            }
            spec.path = item.at("path").get<std::string>();
            const auto extension = lower_ascii(std::filesystem::path(spec.path).extension().string());
            if (extension == ".nam") spec.kind = NodeKind::Nam;
            else if (extension == ".wav") spec.kind = NodeKind::Ir;
            else {
                error = "slot path must end in .nam or .wav";
                return false;
            }
            spec.empty = false;
            slots.push_back(std::move(spec));
        }
        return true;
    }

    // Read-only v0.1 compatibility. Missing/null legacy fields produce no
    // slot, preserving the old model -> IR ordering.
    for (const char* key : {"model", "ir"}) {
        if (!j.contains(key) || j.at(key).is_null()) continue;
        if (!j.at(key).is_string()) {
            error = std::string("legacy ") + key + " must be a string or null";
            return false;
        }
        SlotSpec spec;
        spec.path = j.at(key).get<std::string>();
        const auto extension = lower_ascii(std::filesystem::path(spec.path).extension().string());
        if (extension == ".nam") spec.kind = NodeKind::Nam;
        else if (extension == ".wav") spec.kind = NodeKind::Ir;
        else {
            error = std::string("legacy ") + key + " has an unsupported extension";
            return false;
        }
        spec.empty = false;
        slots.push_back(std::move(spec));
    }
    return true;
}

static bool resolve_slot_specs(std::vector<SlotSpec>& slots, bool allowExternal,
                               std::string& error) {
    for (auto& slot : slots) {
        if (slot.empty) continue;
        std::string resolved;
        if (!resolve_asset_path(slot.path, "data/tones", allowExternal, true,
                                resolved, error)) {
            error = "slot " + slot.path + ": " + error;
            return false;
        }
        slot.path = std::move(resolved);
    }
    return true;
}

static bool parse_input_spec(const nlohmann::json& j, bool allowExternal,
                             InputSpec& input, std::string& error) {
    if (!j.contains("input")) return true;
    const auto& value = j.at("input");
    if (!value.is_object()) {
        error = "input must be an object";
        return false;
    }
    std::string source = "instrument";
    if (value.contains("source")) {
        if (!value.at("source").is_string()) {
            error = "input.source must be a string";
            return false;
        }
        source = value.at("source").get<std::string>();
    }
    if (source != "instrument" && source != "file") {
        error = "input.source must be instrument or file";
        return false;
    }
    const bool hasFile = value.contains("file") && !value.at("file").is_null();
    std::string file;
    if (hasFile) {
        if (!value.at("file").is_string()) {
            error = "input.file must be a string or null";
            return false;
        }
        file = value.at("file").get<std::string>();
    }
    std::string state = "stopped";
    if (value.contains("state")) {
        if (!value.at("state").is_string()) {
            error = "input.state must be a string";
            return false;
        }
        state = value.at("state").get<std::string>();
    }
    if (state != "stopped" && state != "playing" && state != "paused") {
        error = "input.state is invalid";
        return false;
    }
    bool loop = false;
    if (value.contains("loop")) {
        if (!value.at("loop").is_boolean()) {
            error = "input.loop must be boolean";
            return false;
        }
        loop = value.at("loop").get<bool>();
    }

    input.isFile = source == "file";
    input.state = state == "playing" ? 1 : state == "paused" ? 2 : 0;
    input.loop = loop;
    if (!input.isFile) {
        if (hasFile || input.state != 0 || input.loop) {
            error = "instrument input cannot have a file, playback or loop state";
            return false;
        }
        return true;
    }
    if (file.empty() || lower_ascii(std::filesystem::path(file).extension().string()) != ".wav") {
        error = "file input requires a .wav file";
        return false;
    }
    if (!resolve_asset_path(file, "data/dry_inputs", allowExternal, false,
                            input.path, error)) {
        return false;
    }
    return true;
}

static bool read_chain_number(const nlohmann::json& j, const char* key,
                              float defaultValue, float minValue, float maxValue,
                              float& result, std::string& error) {
    result = defaultValue;
    if (!j.contains(key)) return true;
    if (!j.at(key).is_number()) {
        error = std::string(key) + " must be a number";
        return false;
    }
    result = j.at(key).get<float>();
    if (!std::isfinite(result) || result < minValue || result > maxValue) {
        error = std::string(key) + " is out of range";
        return false;
    }
    return true;
}

static bool read_chain_revision(const nlohmann::json& j, uint64_t& revision,
                                std::string& error) {
    revision = 0;
    if (!j.contains("revision")) return true;
    const auto& value = j.at("revision");
    if (value.is_number_unsigned()) {
        revision = value.get<uint64_t>();
        return true;
    }
    if (value.is_number_integer()) {
        const int64_t signedValue = value.get<int64_t>();
        if (signedValue >= 0) {
            revision = (uint64_t)signedValue;
            return true;
        }
    }
    error = "revision must be a non-negative integer";
    return false;
}

static std::string chain_transaction_id(const nlohmann::json& j) {
    if (!j.is_object() || !j.contains("_transaction_id")
        || !j.at("_transaction_id").is_string())
        return {};
    return j.at("_transaction_id").get<std::string>();
}

static std::vector<std::string> slot_signature(const std::vector<SlotSpec>& slots) {
    std::vector<std::string> signature;
    signature.reserve(slots.size());
    for (const auto& slot : slots) {
        if (slot.empty) signature.emplace_back("empty");
        else signature.push_back((slot.kind == NodeKind::Nam ? "nam:" : "ir:") + slot.path);
    }
    return signature;
}

static std::shared_ptr<NeuralAudio::NeuralModel>
make_nam(const std::string& path, NeuralAudio::NeuralModelLoader& loader,
         std::string& error) {
    std::ifstream stream(path, std::ifstream::binary);
    if (!stream) {
        error = "cannot open NAM";
        return nullptr;
    }
    try {
        // Pass a canonical lowercase extension so .NAM remains supported even
        // though NeuralAudio's loader compares the extension literally.
        auto* raw = loader.CreateFromStream(stream, std::filesystem::path("model.nam"));
        if (!raw) {
            error = "NAM loader rejected the file";
            return nullptr;
        }
        return std::shared_ptr<NeuralAudio::NeuralModel>(raw);
    } catch (const std::exception& exception) {
        error = std::string("NAM load failed: ") + exception.what();
        return nullptr;
    }
}

static bool build_prepared_chain(const std::vector<SlotSpec>& slots, float quality,
                                 uint64_t revision, NeuralAudio::NeuralModelLoader& loader,
                                 int sr, std::shared_ptr<PreparedChain>& result,
                                 std::string& error) {
    auto chain = std::make_shared<PreparedChain>();
    chain->quality = quality;
    chain->revision = revision;
    chain->signature = slot_signature(slots);
    chain->nodes.reserve(slots.size());
    for (const auto& slot : slots) {
        if (slot.empty) continue;
        ChainNode node;
        node.kind = slot.kind;
        node.path = slot.path;
        if (slot.kind == NodeKind::Nam) {
            node.nam = make_nam(slot.path, loader, error);
            if (!node.nam) {
                error = "slot preparation failed for " + slot.path + ": " + error;
                return false;
            }
            if (node.nam->HasQualityScaling())
                node.nam->SetQualityScaleFactor(quality);
        } else {
            node.ir = make_ir(slot.path, sr);
            if (!node.ir) {
                error = "slot preparation failed for " + slot.path + ": invalid WAV/IR";
                return false;
            }
        }
        chain->nodes.push_back(std::move(node));
    }
    result = std::move(chain);
    return true;
}

// Managed TUI commits use this path before touching live_chain.json. It
// validates the complete candidate and constructs every NAM/IR node, but does
// not publish audio state or mutate the current chain.
static bool preflight_chain(const nlohmann::json& j,
                            NeuralAudio::NeuralModelLoader& loader,
                            int sr, std::string& error,
                            std::shared_ptr<PreparedChain>* preparedResult = nullptr,
                            bool allowExternalPaths = false) {
    if (!j.is_object()) {
        error = "chain must be an object";
        return false;
    }
    std::vector<SlotSpec> slots;
    if (!parse_slot_specs(j, slots, error)
        || !resolve_slot_specs(slots, allowExternalPaths, error))
        return false;

    float gain = 1.0f, master = 1.0f, quality = 1.0f;
    if (!read_chain_number(j, "gain", 1.0f, 0.0f, 10.0f, gain, error)
        || !read_chain_number(j, "master", 1.0f, 0.0f, 10.0f, master, error)
        || !read_chain_number(j, "quality", 1.0f, 0.0f, 1.0f, quality, error))
        return false;
    bool mute = false;
    if (j.contains("mute") && !j.at("mute").is_boolean()) {
        error = "mute must be boolean";
        return false;
    }
    if (j.contains("mute")) mute = j.at("mute").get<bool>();
    uint64_t revision = 0;
    if (!read_chain_revision(j, revision, error)) return false;
    InputSpec input;
    if (!parse_input_spec(j, allowExternalPaths, input, error)) return false;

    if (input.isFile) {
        std::error_code inputError;
        const bool exists = std::filesystem::exists(input.path, inputError);
        if (inputError) {
            error = "dry input cannot be inspected: " + input.path;
            return false;
        }
        if (exists) {
            WavInput dryInput;
            if (!dryInput.load(input.path, sr)) {
                error = "dry input could not be loaded: " + input.path;
                return false;
            }
        }
    }

    std::shared_ptr<PreparedChain> prepared;
    if (!build_prepared_chain(slots, quality, revision, loader, sr,
                              prepared, error))
        return false;
    if (preparedResult != nullptr) *preparedResult = prepared;
    (void)gain;
    (void)master;
    (void)mute;
    (void)input;
    return true;
}

static bool apply_chain(const nlohmann::json& j, NeuralAudio::NeuralModelLoader& loader,
                        Ctx& ctx, int sr, bool allowExternalPaths = false,
                        bool requireManagedPrepare = false) {
    if (!j.is_object()) {
        fprintf(stderr, "[live] chain must be an object\n");
        return false;
    }
    std::string error;
    std::vector<SlotSpec> slots;
    if (!parse_slot_specs(j, slots, error) || !resolve_slot_specs(slots, allowExternalPaths, error)) {
        fprintf(stderr, "[live] chain rejected: %s\n", error.c_str());
        return false;
    }
    float gain = 1.0f, master = 1.0f, quality = 1.0f;
    if (!read_chain_number(j, "gain", 1.0f, 0.0f, 10.0f, gain, error) ||
        !read_chain_number(j, "master", 1.0f, 0.0f, 10.0f, master, error) ||
        !read_chain_number(j, "quality", 1.0f, 0.0f, 1.0f, quality, error)) {
        fprintf(stderr, "[live] chain rejected: %s\n", error.c_str());
        return false;
    }
    bool mute = false;
    if (j.contains("mute")) {
        if (!j.at("mute").is_boolean()) {
            fprintf(stderr, "[live] chain rejected: mute must be boolean\n");
            return false;
        }
        mute = j.at("mute").get<bool>();
    } else if (master == 0.0f) {
        // v0.1 represented MUTE by master=0. A v0.2 writer must include an
        // explicit mute=false when it intentionally sets the parameter to 0.
        master = 1.0f;
        mute = true;
    }
    uint64_t revision = 0;
    if (!read_chain_revision(j, revision, error)) {
        fprintf(stderr, "[live] chain rejected: %s\n", error.c_str());
        return false;
    }
    InputSpec input;
    if (!parse_input_spec(j, allowExternalPaths, input, error)) {
        fprintf(stderr, "[live] chain rejected: %s\n", error.c_str());
        return false;
    }

    auto current = std::atomic_load(&ctx.chain);
    const auto signature = slot_signature(slots);
    const bool chainChanged = !current || current->signature != signature;
    std::shared_ptr<PreparedChain> next;
    const auto transactionId = chain_transaction_id(j);
    const bool matchesPreflight = (
        !transactionId.empty()
        && transactionId == ctx.managedPreparedTransactionId
        && ctx.managedPreparedChain
        && ctx.managedPreparedChain->revision == revision
        && ctx.managedPreparedChain->quality == quality
        && ctx.managedPreparedChain->signature == signature);
    if (requireManagedPrepare && !matchesPreflight) {
        fprintf(stderr,
                "[live] managed chain rejected: candidate does not match prepared transaction\n");
        return false;
    }
    if (matchesPreflight) {
        next = std::move(ctx.managedPreparedChain);
        ctx.managedPreparedTransactionId.clear();
    } else if (chainChanged) {
        if (!build_prepared_chain(slots, quality, revision, loader,
                                  sr, next, error)) {
            fprintf(stderr, "[live] chain rejected: %s\n", error.c_str());
            return false;
        } else {
            ctx.managedPreparedChain.reset();
            ctx.managedPreparedTransactionId.clear();
        }
    } else {
        if (chain_transaction_id(j) == ctx.managedPreparedTransactionId) {
            ctx.managedPreparedChain.reset();
            ctx.managedPreparedTransactionId.clear();
        }
        // Parameter/revision-only updates reuse the prepared nodes. Quality is
        // applied by the callback at the next block boundary, so the main
        // thread never mutates a model while it is being processed.
        next = std::make_shared<PreparedChain>(*current);
        next->quality = quality;
        next->revision = revision;
    }
    next->gain = gain;
    next->master = master;
    next->mute = mute;

    auto nextWav = std::atomic_load(&ctx.wavIn);
    const bool inputPathChanged = input.isFile && input.path != ctx.liveInputPath;
    if (input.isFile) {
        std::error_code inputError;
        const bool exists = std::filesystem::exists(input.path, inputError);
        if (inputError) {
            fprintf(stderr, "[live] chain rejected: dry input cannot be inspected: %s\n",
                    input.path.c_str());
            return false;
        }
        if (!exists) {
            // A legal but not-yet-downloaded dry input remains part of the
            // chain. Keep the runtime stopped until the file becomes usable.
            nextWav.reset();
            fprintf(stderr, "[live] dry input unavailable: %s\n", input.path.c_str());
        } else if (inputPathChanged || !nextWav) {
            auto candidate = std::make_shared<WavInput>();
            if (candidate->load(input.path, sr)) {
                nextWav = std::move(candidate);
            } else {
                fprintf(stderr, "[live] chain rejected: dry input could not be loaded: %s\n",
                        input.path.c_str());
                return false;
            }
        }
    }
    const bool sourceChanged = input.isFile != ctx.inIsFile.load(std::memory_order_relaxed);
    if (current && (chainChanged || sourceChanged || inputPathChanged))
        request_fade_and_wait(ctx);

    // The only runtime publication point: all nodes and the input file have
    // been prepared, so readers see either the old complete state or this one.
    std::atomic_store(&ctx.chain, next);
    ctx.runtimeRevision.store(revision, std::memory_order_release);
    ctx.runtimeTransactionId = chain_transaction_id(j);
    ++ctx.runtimeAckSequence;
    ctx.runtimeStatus = "applied";
    std::atomic_store(&ctx.wavIn, nextWav);
    ctx.liveInputPath = input.isFile ? input.path : "";
    ctx.inIsFile.store(input.isFile, std::memory_order_release);
    ctx.loop.store(input.isFile && input.loop, std::memory_order_release);
    const int previousState = ctx.playState.load(std::memory_order_relaxed);
    const int effectiveState = input.isFile && nextWav ? input.state : 0;
    if (effectiveState == 1 && previousState == 0 && nextWav)
        nextWav->reset();
    ctx.playState.store(effectiveState, std::memory_order_release);
    fprintf(stderr, "[live] chain revision %llu: %zu slot(s), %zu node(s)\n",
            (unsigned long long)revision, slots.size(), next->nodes.size());
    return true;
}

static bool write_control_reply(const std::filesystem::path& path,
                                const nlohmann::json& payload) {
    const auto temporary = path.string() + ".engine.tmp";
    std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
    if (!stream) return false;
    stream << payload.dump(2) << "\n";
    stream.flush();
    if (!stream) return false;
    stream.close();
    std::error_code ec;
    std::filesystem::rename(temporary, path, ec);
    if (ec) {
        std::remove(temporary.c_str());
        return false;
    }
    return true;
}

static bool read_text_file(const std::filesystem::path& path,
                           std::string& content) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) return false;
    content.assign(std::istreambuf_iterator<char>(stream),
                   std::istreambuf_iterator<char>());
    return true;
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
// mono 输入(scratch) → gain → slot[0..n] → 去咔哒 fade → master/mute → out
static void process_block(const float* in, float* out, int frames, Ctx& c) {
    auto chain = std::atomic_load(&c.chain);
    const float inputGain = chain ? chain->gain : 1.0f;
    if (inputGain != 1.0f)
        for (int i = 0; i < frames; i++) c.scratch[i] = in[i] * inputGain;
    else if (in != c.scratch.data())
        std::copy(in, in + frames, c.scratch.begin());
    c.inPeak.store(block_peak(c.scratch.data(), frames), std::memory_order_relaxed);
    if (chain) {
        for (auto& node : chain->nodes) {
            if (node.kind == NodeKind::Nam && node.nam) {
                // Quality changes are applied by the audio thread at a block
                // boundary. This preserves node reuse without racing NAM's
                // mutable quality state from the live-file watcher.
                if (node.nam->HasQualityScaling() &&
                    node.nam->GetQualityScaleFactor() != chain->quality) {
                    node.nam->SetQualityScaleFactor(chain->quality);
                }
                node.nam->Process(c.scratch.data(), c.scratch.data(), frames);
            } else if (node.kind == NodeKind::Ir && node.ir) {
                // FirFilter is sample-aligned and supports in-place processing;
                // no additional audio block or staging buffer is introduced.
                node.ir->process(c.scratch.data(), c.scratch.data(), frames);
            }
        }
        std::copy(c.scratch.begin(), c.scratch.begin() + frames, out);
    } else {
        std::copy(c.scratch.begin(), c.scratch.begin() + frames, out);
    }
    apply_fade(out, frames, c);
    const float master = chain ? chain->master : 1.0f;
    if (chain && chain->mute) {
        std::fill(out, out + frames, 0.0f);
    } else if (master != 1.0f) {
        for (int i = 0; i < frames; i++) out[i] *= master;
    }
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
    if (channels == 0 || sampleRate == 0 ||
        (fmtTag != 1 && fmtTag != 3) || (bits != 16 && bits != 24 && bits != 32)) {
        fclose(f);
        return false;
    }
    std::vector<uint8_t> raw(dataSize);
    if (fread(raw.data(), 1, dataSize, f) != dataSize) { fclose(f); return false; }
    fclose(f);
    const uint32_t frameBytes = channels * (bits / 8);
    if (frameBytes == 0 || dataSize % frameBytes != 0) return false;
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
    const char* controlPath = nullptr;
    bool managedLive = false;
    if (argc >= 2 && !strcmp(argv[1], "--list")) listOnly = true;
    const char* modelPath = argv[1][0] == '-' ? nullptr : argv[1];
    // argv[2] 是 IR 路径；以 '-' 开头则视为 flag（无 IR 时 --in 等不会误判）
    const char* irPath = (modelPath && argc > 2 && argv[2][0] != '-') ? argv[2] : nullptr;
    int block = 256, sr = 48000;
    // 从 1 开始：model 可能是位置参数（argv[1]）也可能是 flag（--live 模式）；位置参数非 flag 自然跳过
    auto next_arg = [&](int& index, const char* option) -> const char* {
        if (index + 1 >= argc) {
            fprintf(stderr, "%s requires a value\n", option);
            return nullptr;
        }
        return argv[++index];
    };
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--in")) {
            const char* value = next_arg(i, "--in");
            if (!value) return 1;
            inName = value;
        }
        else if (!strcmp(argv[i], "--out")) {
            const char* value = next_arg(i, "--out");
            if (!value) return 1;
            outName = value;
        }
        else if (!strcmp(argv[i], "--ch")) {
            const char* value = next_arg(i, "--ch");
            if (!value) return 1;
            inCh = atoi(value);
        }
        else if (!strcmp(argv[i], "--gain")) {
            const char* value = next_arg(i, "--gain");
            if (!value) return 1;
            gainArg = (float)atof(value);
        }
        else if (!strcmp(argv[i], "--master")) {
            const char* value = next_arg(i, "--master");
            if (!value) return 1;
            masterArg = (float)atof(value);
        }
        else if (!strcmp(argv[i], "--live")) {
            const char* value = next_arg(i, "--live");
            if (!value) return 1;
            livePath = value;
        }
        else if (!strcmp(argv[i], "--managed")) { managedLive = true; }
        else if (!strcmp(argv[i], "--control-file")) {
            const char* value = next_arg(i, "--control-file");
            if (!value) return 1;
            controlPath = value;
        }
        else if (!strcmp(argv[i], "--root")) {
            const char* value = next_arg(i, "--root");
            if (!value) return 1;
            g_root = value;
            g_rootExplicit = true;
        }
        else if (!strcmp(argv[i], "--level-file")) {
            const char* value = next_arg(i, "--level-file");
            if (!value) return 1;
            levelFile = value;
        }
        else if (!strcmp(argv[i], "--record-out")) {
            const char* value = next_arg(i, "--record-out");
            if (!value) return 1;
            recordOut = value;
        }
        else if (!strcmp(argv[i], "--list")) { listOnly = true; }
        else if (!strcmp(argv[i], "--block")) {
            const char* value = next_arg(i, "--block");
            if (!value) return 1;
            block = atoi(value);
        }
        else if (!strcmp(argv[i], "--sr")) {
            const char* value = next_arg(i, "--sr");
            if (!value) return 1;
            sr = atoi(value);
        }
        else if (argv[i][0] >= '0' && argv[i][0] <= '9') {
            if (block == 256) block = atoi(argv[i]);
            else sr = atoi(argv[i]);
        }
    }
    if (managedLive && !controlPath) {
        fprintf(stderr, "--managed requires --control-file\n");
        return 1;
    }
    if (!g_rootExplicit) {
        const auto executable = std::filesystem::path(executable_dir());
        if (executable != ".") g_root = executable.parent_path().string();
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
    loader.SetExternalSampleRate(sr);

    Ctx ctx;
    ctx.scratch.assign(block, 0.0f);
    ctx.runtimeSessionId = std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count()) + "-"
        + std::to_string(reinterpret_cast<uintptr_t>(&ctx));

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
        nlohmann::json initial;
        try {
            const auto initialPath = resolve_path(livePath);
            std::ifstream initialFile(initialPath);
            if (!initialFile) {
                std::error_code fileError;
                if (std::filesystem::exists(initialPath, fileError)
                    || fileError) {
                    return fail("live 配置无法打开", initialPath.c_str());
                }
                // A missing managed file means a valid direct-through chain,
                // not a startup error. The next managed file update still
                // has to pass the normal prepare handshake.
                initial = nlohmann::json::object();
                initial["slots"] = nlohmann::json::array();
                initial["gain"] = 1.0f;
                initial["master"] = 1.0f;
                initial["quality"] = 1.0f;
                initial["mute"] = false;
                initial["revision"] = 0;
            } else {
                initial = nlohmann::json::parse(initialFile);
            }
            // Startup is always non-managed; managed updates begin only after
            // a later candidate passes the prepare handshake.
            if (!apply_chain(initial, loader, ctx, sr, false, false)) {
                return fail("live 配置被拒绝");
            }
            ctx.runtimeStatus = "applied";
        } catch (const std::exception& e) {
            return fail("live 配置解析失败", e.what());
        }
    } else {
        if (!modelPath) return fail("非 live 模式需要 model.nam");
        nlohmann::json initial = nlohmann::json::object();
        initial["slots"] = nlohmann::json::array();
        initial["slots"].push_back({{"path", modelPath}});
        if (irPath) initial["slots"].push_back({{"path", irPath}});
        initial["gain"] = gainArg;
        initial["master"] = masterArg;
        initial["quality"] = 1.0f;
        initial["mute"] = false;
        initial["revision"] = 0;
        if (!apply_chain(initial, loader, ctx, sr, true)) {
            return fail("模型或 IR 加载失败");
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
            modelPath ? modelPath : "<live chain>", sr, block, block * 1000.0 / sr);
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
    if (livePath && managedLive)
        fprintf(stderr, "managed live transaction acknowledgements enabled\n");
    fprintf(stderr, "电平监控（0.1 秒刷新，Ctrl+C 退出）:\n");
    signal(SIGINT, on_sigint);
    std::string lastLivePayload;
    std::string lastControlPayload;
    std::filesystem::path controlReplyPath;
    if (livePath) {
        read_text_file(resolve_path(livePath), lastLivePayload);
    }
    if (controlPath) {
        controlReplyPath = std::filesystem::path(resolve_path(controlPath));
        // Python's sidecar is live_control.reply.json for a
        // live_control.json request. Replace the request suffix instead of
        // appending to it, so both ends use one deterministic channel name.
        if (controlReplyPath.extension() == ".json") {
            controlReplyPath.replace_filename(
                controlReplyPath.stem().string() + ".reply.json");
        } else {
            controlReplyPath += ".reply.json";
        }
        read_text_file(resolve_path(controlPath), lastControlPayload);
        if (managedLive) {
            write_control_reply(controlReplyPath, {
                {"status", "ready"},
                {"session_id", ctx.runtimeSessionId},
            });
        }
    }
    for (;;) {
        if (g_stop.load(std::memory_order_relaxed)) break;
        Pa_Sleep(100);
        if (controlPath && managedLive) {
            std::string controlPayload;
            if (read_text_file(resolve_path(controlPath), controlPayload)
                && controlPayload != lastControlPayload) {
                lastControlPayload = controlPayload;
                ctx.managedPreparedChain.reset();
                ctx.managedPreparedTransactionId.clear();
                nlohmann::json response = {
                    {"status", "rejected"},
                    {"session_id", ctx.runtimeSessionId},
                    {"transaction_id", ""},
                    {"revision", 0},
                    {"error", "invalid prepare request"},
                };
                std::string requestTransactionId;
                try {
                    const auto request = nlohmann::json::parse(controlPayload);
                    std::string transactionId;
                    if (request.contains("transaction_id")
                        && request.at("transaction_id").is_string())
                        transactionId = request.at("transaction_id").get<std::string>();
                    requestTransactionId = transactionId;
                    response["transaction_id"] = transactionId;
                    const auto candidate = request.contains("candidate")
                        ? request.at("candidate") : nlohmann::json::object();
                    std::string operation;
                    if (request.contains("operation")
                        && request.at("operation").is_string())
                        operation = request.at("operation").get<std::string>();
                    uint64_t revision = 0;
                    std::string revisionError;
                    if (!candidate.is_object()
                        || operation != "prepare"
                        || transactionId.empty()) {
                        response["error"] = "invalid prepare request";
                    } else if (!read_chain_revision(candidate, revision,
                                                     revisionError)) {
                        response["transaction_id"] = transactionId;
                        response["error"] = revisionError;
                    } else {
                        std::string error;
                        std::shared_ptr<PreparedChain> preparedChain;
                        const bool prepared = preflight_chain(
                            candidate, loader, sr, error, &preparedChain);
                        response["transaction_id"] = transactionId;
                        response["revision"] = revision;
                        response["status"] = prepared ? "prepared" : "rejected";
                        if (prepared) {
                            ctx.managedPreparedChain = std::move(preparedChain);
                            ctx.managedPreparedTransactionId = transactionId;
                            response.erase("error");
                        } else {
                            ctx.managedPreparedChain.reset();
                            ctx.managedPreparedTransactionId.clear();
                            response["error"] = error;
                        }
                    }
                } catch (const std::exception& exception) {
                    response["transaction_id"] = requestTransactionId;
                    response["error"] = exception.what();
                }
                if (!write_control_reply(controlReplyPath, response))
                    fprintf(stderr, "[live] cannot write managed prepare reply\n");
            }
        }
        if (livePath) {
            std::string livePayload;
            if (read_text_file(resolve_path(livePath), livePayload)
                && livePayload != lastLivePayload) {
                lastLivePayload = livePayload;
                bool applied = false;
                nlohmann::json candidate;
                try {
                    candidate = nlohmann::json::parse(livePayload);
                    applied = apply_chain(candidate, loader, ctx, sr,
                                          false, managedLive);
                } catch (...) {
                    fprintf(stderr, "[live] 配置解析失败\n");
                }
                if (applied) {
                    // apply_chain published the complete runtime candidate and
                    // recorded its transaction identity/ack sequence.
                } else {
                    uint64_t attemptedRevision = 0;
                    std::string revisionError;
                    if (!candidate.is_null())
                        read_chain_revision(candidate, attemptedRevision,
                                            revisionError);
                    ctx.runtimeRevision.store(attemptedRevision,
                                              std::memory_order_release);
                    ctx.runtimeTransactionId = chain_transaction_id(candidate);
                    ctx.runtimeStatus = "rejected";
                    ++ctx.runtimeAckSequence;
                }
            }
        }
        const float inL = ctx.inPeak.load(std::memory_order_relaxed);
        const float outL = ctx.outPeak.load(std::memory_order_relaxed);
        print_levels("VU", inL, outL);
        // 干声回放：播完检测（非循环）→ 自动停止归零；位置秒数回传 TUI
        auto wi = std::atomic_load(&ctx.wavIn);
        if (ctx.inIsFile.load(std::memory_order_relaxed) && wi) {
            if (ctx.playState.load() == 1 && !ctx.loop.load() &&
                wi->position() >= wi->size()) {
                ctx.playState.store(0);
                wi->reset();
                fprintf(stderr, "[live] 干声播完\n");
            }
            ctx.playPosSec.store((float)wi->position() / sr,
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
                nlohmann::json telemetry = {
                    {"in", inL},
                    {"out", outL},
                    {"play_state", ps},
                    {"play_pos", ctx.playPosSec.load()},
                    {"runtime_session_id", ctx.runtimeSessionId},
                    {"runtime_revision", ctx.runtimeRevision.load(
                        std::memory_order_acquire)},
                    {"runtime_status", ctx.runtimeStatus},
                    {"runtime_transaction_id", ctx.runtimeTransactionId},
                    {"runtime_ack_seq", ctx.runtimeAckSequence},
                };
                const auto serialized = telemetry.dump();
                fprintf(lf, "%s\n", serialized.c_str());
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
