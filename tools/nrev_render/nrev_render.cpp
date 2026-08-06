// =============================================================================
// nrev_render.cpp —— 本插件混响的离线渲染器
// -----------------------------------------------------------------------------
// 直接编译 src/dsp 里**发布用**的 ReverbEffect/ReverbCore 头文件，
// 于是「测的就是发布的」：拟合脚本调这个可执行文件，改的是同一份算法常数。
//
// 讲 plugin_match.OfflineRenderer 的 f32 stdin/stdout 协议，
// 与参考侧的 tools/vst3_host/vst3_render 同构 → Python 侧 A/B 代码零分叉。
//
// 参数顺序（全部**归一值 0..1**，与参考插件的 VST3 normalized 语义一致）：
//     drywet  predelay  decay  lowcut  highcut
//     [d_active  d_drywet  d_timel  d_timer  d_feedback  d_lowpass  d_highpass
//      d_stereo]
//
// 后 8 个是延迟段，**可省略**（省略即延迟关闭）—— 这样既有的混响对拍脚本
// 不需要改一个字就继续有效。d_active / d_stereo 按 >0.5 取布尔。
//
// 串联顺序与插件内一致：**延迟 → 混响**（见 PluginProcessor.h 的注释）。
// 单独测延迟段时把混响的 drywet 设 0：ReverbEffect 的干路在 drywet=0 时
// 增益恒 1（实测），故混响成为直通。
//
// 用法：
//   nrev_render <sampleRate> <blockSize> <numChannels> <p0..p4> [<d0..d7>] \
//       < in.f32 > out.f32
//
// 编译见 build.sh（不需要 JUCE：这里用一个最小的 AudioBlock/Context 替身，
// 与模板 templates/offline_renderer.cpp 的做法一致）。
// =============================================================================
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <vector>

// ---- juce::dsp 的最小替身 -------------------------------------------------
// ReverbEffect / DelayEffect 只用到 ProcessSpec 的三个字段与
// context.getOutputBlock() 的 getNumChannels/getNumSamples/getChannelPointer，
// 故这里给出等价的极小实现，避免为离线渲染器拉入整个 JUCE。
namespace juce::dsp
{
struct ProcessSpec
{
    double sampleRate;
    unsigned int maximumBlockSize;
    unsigned int numChannels;
};
} // namespace juce::dsp

namespace
{
struct Block
{
    std::vector<float*> ch;
    size_t n { 0 };
    size_t getNumChannels() const { return ch.size(); }
    size_t getNumSamples() const { return n; }
    float* getChannelPointer(size_t c) const { return ch[c]; }
};

struct Context
{
    Block blk;
    const Block& getOutputBlock() const { return blk; }
};
} // namespace

#include "../../src/dsp/ReverbEffect.h"
#include "../../src/dsp/DelayEffect.h"

int main(int argc, char** argv)
{
    if (argc < 4)
    {
        std::fprintf(stderr,
            "usage: nrev_render <sr> <block> <nch> <drywet> <predelay> <decay>"
            " <lowcut> <highcut>\n"
            "       [<d_active> <d_drywet> <d_timel> <d_timer> <d_feedback>"
            " <d_lowpass> <d_highpass> <d_stereo>]\n"
            "  (all params normalized 0..1; delay group optional, default off)\n");
        return 2;
    }

    const double sr = std::atof(argv[1]);
    const int block = std::atoi(argv[2]);
    const int nch = std::atoi(argv[3]);

    float p[5] = { 0.5f, 0.5f, 0.5f, 0.0f, 1.0f };
    for (int i = 0; i < 5; ++i)
        if (argc > 4 + i) p[i] = static_cast<float>(std::atof(argv[4 + i]));

    // 延迟段：默认全关。d[0] = active，其余与 setParametersNormalized 同序。
    // 默认时长 0.577079952 对应实测的出厂 500 ms（见 DelayEffect.h）。
    float d[8] = { 0.0f, 0.5f, 0.577079952f, 0.577079952f, 0.5f, 1.0f, 0.0f, 1.0f };
    for (int i = 0; i < 8; ++i)
        if (argc > 9 + i) d[i] = static_cast<float>(std::atof(argv[9 + i]));
    const bool delayOn = (d[0] > 0.5f);

    // 第 18 个参数（可选）：延迟 LFO 的起相，单位 = 周期的分数。
    // 只有对拍脚本会传它，用来标定参考侧那个未知但确定的起相；
    // 插件运行时永远是 0。
    const double dLfoPhase = (argc > 17) ? std::atof(argv[17]) : 0.0;

    // ---- 读入全部输入（interleaved f32）----
    std::vector<float> inter;
    {
        std::vector<float> buf(4096);
        size_t r;
        while ((r = std::fread(buf.data(), sizeof(float), buf.size(), stdin)) > 0)
            inter.insert(inter.end(), buf.begin(), buf.begin() + static_cast<long>(r));
    }
    const size_t frames = (nch > 0) ? inter.size() / static_cast<size_t>(nch) : 0;

    std::vector<std::vector<float>> chans(static_cast<size_t>(nch),
                                          std::vector<float>(frames, 0.0f));
    for (size_t i = 0; i < frames; ++i)
        for (int c = 0; c < nch; ++c)
            chans[static_cast<size_t>(c)][i] =
                inter[i * static_cast<size_t>(nch) + static_cast<size_t>(c)];

    // ---- 构造并准备 DSP ----
    nrev::ReverbEffect fx;
    juce::dsp::ProcessSpec spec { sr, static_cast<unsigned>(block),
                                  static_cast<unsigned>(nch) };
    fx.prepare(spec);
    fx.setParametersNormalized(p[0], p[1], p[2], p[3], p[4]);

    nrev::DelayEffect dly;
    if (delayOn)
    {
        dly.prepare(spec);
        dly.setParametersNormalized(d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
        // prepare() 内部会 reset() 把相位归零，所以起相必须在它**之后**设。
        dly.setLfoPhase(dLfoPhase);
    }

    for (size_t pos = 0; pos < frames; pos += static_cast<size_t>(block))
    {
        const size_t n = std::min(static_cast<size_t>(block), frames - pos);
        Context ctx;
        ctx.blk.n = n;
        ctx.blk.ch.clear();
        for (int c = 0; c < nch; ++c)
            ctx.blk.ch.push_back(chans[static_cast<size_t>(c)].data() + pos);
        // 顺序与插件内一致：延迟 → 混响
        if (delayOn) dly.process(ctx);
        fx.process(ctx);
    }

    std::vector<float> out(frames * static_cast<size_t>(nch));
    for (size_t i = 0; i < frames; ++i)
        for (int c = 0; c < nch; ++c)
            out[i * static_cast<size_t>(nch) + static_cast<size_t>(c)] =
                chans[static_cast<size_t>(c)][i];
    std::fwrite(out.data(), sizeof(float), out.size(), stdout);
    return 0;
}
