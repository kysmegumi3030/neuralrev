/**
 * @file  cand_delay_blocks.cpp
 * @brief 逐块拆解候选侧环路：把「实测 L(f) 比模型少 0.29 dB」归因到具体的块
 *
 * cand_delay_selftest 的第 2 项发现候选实测的每圈损耗比**候选自己的解析模型**
 * 还要小（8 kHz 上少 0.29 dB）。少 ≠ 多：内插器只会加损耗，所以这不是
 * 「滤波器没生效」那么简单，必须拆开看。
 *
 * 三块分别单独测，各自与解析值对照：
 *   A. **四级 biquad 链**（fixedA→fixedB→userLP→userHP）：冲激响应 → FFT，
 *      与 RBJ 解析式比。这一块若对得上，说明系数换算没问题。
 *   B. **延迟线本身，LFO 关掉**（amp=0）：应当是纯延迟，幅度响应恒 1。
 *      若不是 1，就是 15 阶 Lagrange 在 frac 固定值上的损耗。
 *   C. **延迟线，LFO 开着**：时变延迟会把能量搬到 ±LFO 边带，
 *      单频投影读到的载波会被抽薄（Bessel 型）。这一项量化那个抽薄量。
 *
 * 关键：C 的效应在**参考侧同样存在**（参考也有同一个 LFO），所以它不该
 * 计入「候选 vs 参考」的差；但它会污染「候选实测 vs 候选解析模型」的比较。
 * 本脚本的目的就是把这两者分开。
 *
 * 编译：
 *   c++ -std=c++17 -O2 -DNREV_NO_JUCE -I src/dsp \
 *       tools/measure/cand_delay_blocks.cpp -o /tmp/cand_delay_blocks
 */

#include <cmath>
#include <complex>
#include <cstdio>
#include <string>
#include <vector>

#include "DelayCore.hpp"

using namespace nrev;
namespace DT = nrev::delaytuning;

static constexpr double SR = 48000.0;

// ------------------------------------------------------------ 解析式
/// RBJ 二阶的幅度响应（dB），数字域精确式（与 fit_delay_loop_filter.py 同一式）
static double biquadDb(double fc, double q, double f, bool hp)
{
    const double w0 = 2.0 * M_PI * fc / SR;
    const double cs = std::cos(w0), sn = std::sin(w0);
    const double al = sn / (2.0 * q);
    const double a0 = 1.0 + al;
    double b0, b1, b2;
    if (hp) { b0 = ((1.0 + cs) * 0.5) / a0; b1 = (-(1.0 + cs)) / a0; b2 = b0; }
    else    { b0 = ((1.0 - cs) * 0.5) / a0; b1 = (1.0 - cs) / a0;    b2 = b0; }
    const double a1 = (-2.0 * cs) / a0, a2 = (1.0 - al) / a0;
    const std::complex<double> z = std::exp(std::complex<double>(0.0, -2.0 * M_PI * f / SR));
    const auto h = (b0 + b1 * z + b2 * z * z) / (1.0 + a1 * z + a2 * z * z);
    return 20.0 * std::log10(std::abs(h) + 1e-30);
}

/// 单频最小二乘投影（与两侧测量脚本同一个 band_amp）
static double bandAmp(const std::vector<float>& x, size_t a, size_t n, double f)
{
    double ss = 0, sc = 0, s2 = 0, c2 = 0;
    for (size_t i = 0; i < n; ++i)
    {
        const double t = 2.0 * M_PI * f * static_cast<double>(i) / SR;
        ss += x[a + i] * std::sin(t); sc += x[a + i] * std::cos(t);
        s2 += std::sin(t) * std::sin(t); c2 += std::cos(t) * std::cos(t);
    }
    return std::hypot(ss / s2, sc / c2);
}

static const double FREQS[] = { 100, 200, 350, 500, 700, 1000, 1500, 2000,
                               3000, 4000, 5000, 6000, 8000 };
static constexpr int NF = 13;

static void hdr(const char* t)
{
    printf("\n%s\n%s\n%s\n", std::string(84, '=').c_str(), t, std::string(84, '=').c_str());
}

// ------------------------------------------------ A. 四级 biquad 链
static void testFilterChain()
{
    hdr("A. 四级 biquad 链的冲激响应 vs 解析式（检验系数换算与 float 状态）");

    DelayBiquad fa, fb, lp, hp;
    fa.setLowpass(DT::kFitLoopFixedLpAHz, DT::kFitLoopFixedLpAQ, SR);
    fb.setLowpass(DT::kFitLoopFixedLpBHz, DT::kFitLoopFixedLpBQ, SR);
    lp.setLowpass(DT::lowpassHzFromNorm(1.0), DT::kMeasUserFilterQ, SR);
    hp.setHighpass(DT::highpassHzFromNorm(0.0), DT::kMeasUserFilterQ, SR);

    // 冲激响应（长度足够让 20 Hz 的 HP 尾巴衰完）
    const size_t N = 1 << 18;
    std::vector<float> h(N, 0.0f);
    h[0] = 1.0f;
    for (size_t i = 0; i < N; ++i)
        h[i] = hp.process(lp.process(fb.process(fa.process(h[i]))));

    printf("  %7s %10s %10s %9s\n", "频率", "实现 dB", "解析 dB", "差");
    double worst = 0.0;
    for (int i = 0; i < NF; ++i)
    {
        // DTFT 单点（冲激响应，故直接求和即可）
        std::complex<double> acc(0.0, 0.0);
        for (size_t n = 0; n < N; ++n)
            acc += static_cast<double>(h[n])
                 * std::exp(std::complex<double>(0.0, -2.0 * M_PI * FREQS[i]
                                                       * static_cast<double>(n) / SR));
        const double impl = 20.0 * std::log10(std::abs(acc) + 1e-30);
        const double ana = biquadDb(DT::kFitLoopFixedLpAHz, DT::kFitLoopFixedLpAQ, FREQS[i], false)
                         + biquadDb(DT::kFitLoopFixedLpBHz, DT::kFitLoopFixedLpBQ, FREQS[i], false)
                         + biquadDb(DT::lowpassHzFromNorm(1.0), DT::kMeasUserFilterQ, FREQS[i], false)
                         + biquadDb(DT::highpassHzFromNorm(0.0), DT::kMeasUserFilterQ, FREQS[i], true);
        if (std::fabs(impl - ana) > worst) worst = std::fabs(impl - ana);
        printf("  %7.0f %10.4f %10.4f %+9.4f\n", FREQS[i], impl, ana, impl - ana);
    }
    printf("\n  最差偏差 = %.4f dB   %s\n", worst,
           worst < 0.02 ? "OK 系数换算无误" : "FAIL biquad 实现与解析式不符");
}

// ------------------------------------------------ B/C. 延迟线
/// 让信号在延迟线里绕 rounds 圈（无滤波、无反馈衰减），读单频幅度比。
/// lfoAmp=0 → 纯延迟；lfoAmp>0 → 含时变调制。
static void lineRoundTrips(double lfoAmp, double delaySamples, int rounds)
{
    printf("  LFO 幅度 = %.5f samples   基准延迟 D = %.0f samples   圈数 = %d\n",
           lfoAmp, delaySamples, rounds);
    printf("  %7s %11s %11s\n", "频率", "每圈 dB", "累计 dB");

    for (int i = 0; i < NF; ++i)
    {
        const double f = FREQS[i];
        const int BURST = 2048;
        const size_t N = static_cast<size_t>(delaySamples) * (rounds + 2) + 8192;
        std::vector<float> x(N, 0.0f);
        for (int k = 0; k < BURST; ++k)
        {
            const double w = 0.5 - 0.5 * std::cos(2.0 * M_PI * k / (BURST - 1));
            x[static_cast<size_t>(1024 + k)] = static_cast<float>(
                1e-3 * w * std::sin(2.0 * M_PI * f * k / SR));
        }

        const double a0 = bandAmp(x, 1024, static_cast<size_t>(BURST), f);

        // 反复过同一条延迟线：每次把整段重新喂一遍，相位连续
        std::vector<float> cur = x;
        LfoDelayLine line;
        line.prepare(static_cast<int>(delaySamples) + 16,
                     static_cast<int>(std::ceil(4.0 * DT::kMeasLfoAmpSamples)) + 8);
        line.setDelay(delaySamples);
        line.setLfo(DT::kMeasLfoRateHz, lfoAmp, SR);

        double lastAmp = a0;
        double totalDb = 0.0;
        for (int rr = 0; rr < rounds; ++rr)
        {
            std::vector<float> out(N, 0.0f);
            for (size_t n = 0; n < N; ++n) out[n] = line.process(cur[n]);
            // 猝发中心搬到 1024 + (rr+1)*D
            const size_t c = static_cast<size_t>(1024 + (rr + 1) * delaySamples);
            const double amp = bandAmp(out, c - 300, static_cast<size_t>(BURST) + 600, f);
            totalDb = 20.0 * std::log10(amp / a0 + 1e-30);
            lastAmp = amp;
            cur = out;
            line.reset();
            line.setDelay(delaySamples);
            line.setLfo(DT::kMeasLfoRateHz, lfoAmp, SR);
        }
        (void) lastAmp;
        printf("  %7.0f %11.4f %11.4f\n", f, totalDb / rounds, totalDb);
    }
}

int main()
{
    printf("候选侧延迟环路的逐块拆解\n");
    testFilterChain();

    const double D = DT::timeMsFromNorm(0.4) * 1.0e-3 * SR;

    hdr("B. 延迟线，LFO 关掉（amp=0）：应当是纯延迟，每圈 0.0000 dB");
    lineRoundTrips(0.0, D, 5);

    hdr("C. 延迟线，LFO 开着：时变延迟把载波搬进边带，单频投影会读到「损耗」");
    lineRoundTrips(DT::kMeasLfoAmpSamples, D, 5);

    printf("\n判读：若 A 对得上、B 为 0、C 明显非 0，则 selftest 第 2 项的\n");
    printf("「实测比模型少」来自 C 那一项在两侧的**不对称**，而不是滤波器系数。\n");
    return 0;
}
